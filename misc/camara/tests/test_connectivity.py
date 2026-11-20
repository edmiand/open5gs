"""Phase 5: Application Profiles + Connectivity Insights."""

import pytest

from camara_gw.apis.connectivity import MEETS, UNABLE, assess
from camara_gw.probe import PingResult

DEV = {"phoneNumber": "+99970000001"}
STRICT = {"packetDelayBudget": {"value": 5, "unit": "Milliseconds"}, "jitter": {"value": 2, "unit": "Milliseconds"},
          "packetLossErrorRate": 2}
LENIENT = {"packetDelayBudget": {"value": 100, "unit": "Milliseconds"}, "jitter": {"value": 50, "unit": "Milliseconds"}}


class FakeProber:
    def __init__(self, rtts, sent=20, tp=(None, None)):
        self.rtts, self.sent, self.tp, self.calls = rtts, sent, tp, []

    async def ping(self, ip):
        self.calls.append(ip)
        return PingResult(sent=self.sent, rtts_ms=list(self.rtts))

    async def throughput_bps(self, ip):
        return self.tp


def make_profile(client, auth, nqt):
    r = client.post("/application-profiles/v0.6/application-profiles", json={"networkQualityThresholds": nqt}, headers=auth)
    assert r.status_code == 201, r.text
    return r.json()["applicationProfileId"]


def check(client, auth, pid, device=DEV):
    return client.post("/connectivity-insights/v0.7/check-network-quality",
                       json={"applicationProfileId": pid, "device": device,
                             "applicationServer": {"ipv4Address": "198.51.100.10"}}, headers=auth)


# ── pure assessment ──────────────────────────────────────────────────────────
def test_ping_stats():
    p = PingResult(sent=4, rtts_ms=[2.0, 4.0, 2.0])
    assert p.one_way_ms == 1.0 and p.loss_rate == 0.25 and p.jitter_ms == 2.0


def test_assess_meets_and_unable():
    good = PingResult(sent=20, rtts_ms=[2.0] * 20)
    res, _ = assess(STRICT, good, None, None)
    assert res == {"packetDelayBudget": MEETS, "jitter": MEETS, "packetlossErrorRate": MEETS}
    bad = PingResult(sent=20, rtts_ms=[20.0, 30.0] * 9)               # 10% loss, 10-15 ms one-way, 10 ms jitter
    res, _ = assess(STRICT, bad, None, None)
    assert set(res.values()) == {UNABLE}


def test_assess_throughput_only_when_measured():
    nqt = {"targetMinDownstreamRate": {"value": 10, "unit": "Mbps"}}
    ping = PingResult(sent=5, rtts_ms=[1.0] * 5)
    assert assess(nqt, ping, None, None)[0] == {}                     # not measurable -> omitted
    assert assess(nqt, ping, 20e6, None)[0] == {"targetMinDownstreamRate": MEETS}
    assert assess(nqt, ping, 5e6, None)[0] == {"targetMinDownstreamRate": UNABLE}


def test_assess_unreachable_ue():
    res, _ = assess({**STRICT, "targetMinUpstreamRate": {"value": 1, "unit": "Mbps"}}, PingResult(sent=5), None, None)
    assert set(res.values()) == {UNABLE}


# ── application profiles CRUD ────────────────────────────────────────────────
def test_profile_crud(client, auth):
    pid = make_profile(client, auth, STRICT)
    g = client.get(f"/application-profiles/v0.6/application-profiles/{pid}", headers=auth).json()
    assert g["networkQualityThresholds"]["packetLossErrorRate"] == 2
    r = client.patch(f"/application-profiles/v0.6/application-profiles/{pid}", json={"networkQualityThresholds": LENIENT}, headers=auth)
    assert r.status_code == 200 and "packetLossErrorRate" not in r.json()["networkQualityThresholds"]   # full replace
    assert client.delete(f"/application-profiles/v0.6/application-profiles/{pid}", headers=auth).status_code == 204
    assert client.get(f"/application-profiles/v0.6/application-profiles/{pid}", headers=auth).status_code == 404


@pytest.mark.parametrize("body", [{}, {"networkQualityThresholds": {}},
                                  {"networkQualityThresholds": {"jitter": {"value": 0, "unit": "Milliseconds"}}},
                                  {"networkQualityThresholds": {"jitter": {"value": 5, "unit": "Fortnights"}}},
                                  {"networkQualityThresholds": {"targetMinDownstreamRate": {"value": 2000, "unit": "Mbps"}}}])
def test_profile_validation(client, auth, body):
    r = client.post("/application-profiles/v0.6/application-profiles", json=body, headers=auth)
    assert r.status_code == 400 and r.json()["code"] == "INVALID_ARGUMENT"


# ── check-network-quality ────────────────────────────────────────────────────
def test_check_meets(client, auth):
    client.app.state.prober = FakeProber([1.5] * 20)
    pid = make_profile(client, auth, STRICT)
    r = check(client, auth, pid)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["device"] == DEV and j["packetDelayBudget"] == MEETS and j["packetlossErrorRate"] == MEETS
    assert j["additionalKPIs"]["connectivityType"] == "5G"
    assert client.app.state.prober.calls == ["10.45.0.2"]
    last = client.get("/_lab/connectivity-insights/last", headers=auth).json()
    assert last["measured"]["oneWayDelayMs"] == 0.75 and last["ueIpv4"] == "10.45.0.2"


def test_check_degraded(client, auth):
    client.app.state.prober = FakeProber([40.0, 60.0] * 10)
    pid = make_profile(client, auth, STRICT)
    j = check(client, auth, pid).json()
    assert j["packetDelayBudget"] == UNABLE and j["jitter"] == UNABLE


def test_check_errors(client, auth):
    client.app.state.prober = FakeProber([1.0] * 20)
    assert check(client, auth, "nope").json()["code"] == "INVALID_ARGUMENT"                       # not a UUID
    assert check(client, auth, "6f1c3f3e-0000-4000-8000-000000000000").json()["code"] == "NOT_FOUND"
    pid = make_profile(client, auth, LENIENT)
    assert check(client, auth, pid, {"phoneNumber": "+15550000000"}).json()["code"] == "IDENTIFIER_NOT_FOUND"
    r = client.post("/connectivity-insights/v0.7/check-network-quality",
                    json={"applicationProfileId": pid, "device": DEV, "applicationServer": {}}, headers=auth)
    assert r.status_code == 400


def test_profile_isolated_per_client(client, auth):
    pid = make_profile(client, auth, LENIENT)
    from camara_gw.auth.tokens import issue_token
    other = issue_token(client.app.state.config, "other-app", ["application-profiles:read", "connectivity-insights:check"])
    h = {"Authorization": f"Bearer {other['access_token']}"}
    assert client.get(f"/application-profiles/v0.6/application-profiles/{pid}", headers=h).status_code == 404
    assert check(client, h, pid).status_code == 404
