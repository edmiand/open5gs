"""Phase 1: QoS Profiles + Quality on Demand (PCF/BSF/SMF faked)."""

import time
from datetime import datetime, timedelta, timezone

import pytest

from camara_gw.apis.qod import COLLECTION, PortsSpec, build_flow_descriptions
from tests.conftest import build_client

SUPI1 = "imsi-999700000000001"
BODY = {"device": {"phoneNumber": "+99970000001"}, "applicationServer": {"ipv4Address": "198.51.100.10"},
        "qosProfile": "QOS_E", "duration": 600, "sink": "http://sink.example/events"}


def wait_for(fn, timeout=4.0):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.1)
    raise AssertionError("condition not met in time")


def create(client, auth, **over):
    body = {**BODY, **over}
    return client.post("/quality-on-demand/v1/sessions", json=body, headers=auth)


def make_available(client, auth, core):
    r = create(client, auth)
    assert r.status_code == 201, r.text
    sid = r.json()["sessionId"]
    core.add_flow(SUPI1, qfi=2, fiveqi=3)
    wait_for(lambda: client.get(f"/quality-on-demand/v1/sessions/{sid}", headers=auth).json()["qosStatus"] == "AVAILABLE")
    return sid


# ── QoS Profiles ─────────────────────────────────────────────────────────────
def test_retrieve_all_profiles(client, auth):
    r = client.post("/qos-profiles/v1/retrieve-qos-profiles", json={}, headers=auth)
    assert r.status_code == 200
    names = [p["name"] for p in r.json()]
    assert names == ["QOS_E", "QOS_S", "QOS_M", "QOS_L"]
    qos_l = r.json()[3]
    assert qos_l["maxDownstreamRate"] == {"value": 100, "unit": "Mbps"}
    assert qos_l["maxDuration"] == {"value": 86400, "unit": "Seconds"}


def test_retrieve_profiles_filters(client, auth):
    r = client.post("/qos-profiles/v1/retrieve-qos-profiles", json={"name": "QOS_E", "device": {"phoneNumber": "+99970000001"}}, headers=auth)
    assert [p["name"] for p in r.json()] == ["QOS_E"]
    r = client.post("/qos-profiles/v1/retrieve-qos-profiles", json={"status": "DEPRECATED"}, headers=auth)
    assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND"
    r = client.post("/qos-profiles/v1/retrieve-qos-profiles", json={"device": {"phoneNumber": "+15550000000"}}, headers=auth)
    assert r.json()["code"] == "IDENTIFIER_NOT_FOUND"


def test_get_profile(client, auth):
    assert client.get("/qos-profiles/v1/qos-profiles/QOS_M", headers=auth).json()["name"] == "QOS_M"
    assert client.get("/qos-profiles/v1/qos-profiles/NOPE", headers=auth).status_code == 404


# ── flow descriptions ────────────────────────────────────────────────────────
def test_flows_without_ports():
    assert build_flow_descriptions("10.45.0.2", ["198.51.100.0/24"], None, None) == [
        "permit out ip from 198.51.100.0/24 to 10.45.0.2",
        "permit in ip from 10.45.0.2 to 198.51.100.0/24"]


def test_flows_with_ports():
    asp = PortsSpec.model_validate({"ports": [443], "ranges": [{"from": 5010, "to": 5020}]})
    f = build_flow_descriptions("10.45.0.2", ["198.51.100.10"], asp, None)
    assert "permit out 17 from 198.51.100.10 5010-5020 to 10.45.0.2" in f
    assert "permit in 6 from 10.45.0.2 to 198.51.100.10 443" in f
    assert len(f) == 8          # 2 protos x 2 port tokens x 2 directions


# ── QoD lifecycle ────────────────────────────────────────────────────────────
def test_create_requested_then_available(client, auth, core):
    r = create(client, auth)
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["qosStatus"] == "REQUESTED" and j["device"] == {"phoneNumber": "+99970000001"}
    assert r.headers["location"].endswith(f"/quality-on-demand/v1/sessions/{j['sessionId']}")
    asc = core.pcf_bodies[-1]["ascReqData"]
    mc = asc["medComponents"]["1"]
    assert asc["ueIpv4"] == "10.45.0.2" and asc["dnn"] == "internet" and asc["sliceInfo"] == {"sst": 1}
    assert mc["qosReference"] == "QOS_E" and mc["marBwDl"] == "2 Mbps" and mc["mirBwUl"] == "1 Mbps"
    assert mc["medSubComps"]["1"]["fDescs"] == ["permit out ip from 198.51.100.10 to 10.45.0.2",
                                                "permit in ip from 10.45.0.2 to 198.51.100.10"]
    assert asc["notifUri"].endswith(f"/_internal/pcf/qod/{j['sessionId']}")

    core.add_flow(SUPI1, qfi=2, fiveqi=3)
    sid = j["sessionId"]
    info = wait_for(lambda: (g := client.get(f"/quality-on-demand/v1/sessions/{sid}", headers=auth).json())
                    ["qosStatus"] == "AVAILABLE" and g)
    assert info["startedAt"] and info["expiresAt"]
    ev = wait_for(lambda: core.sink_events)[-1]
    assert ev["type"] == "org.camaraproject.quality-on-demand.v1.qos-status-changed"
    assert ev["data"] == {"sessionId": sid, "qosStatus": "AVAILABLE"}


def test_conflict_second_session(client, auth):
    assert create(client, auth).status_code == 201
    r = create(client, auth)
    assert r.status_code == 409 and r.json()["code"] == "CONFLICT"


def test_delete_releases_pcf_and_notifies(client, auth, core):
    sid = make_available(client, auth, core)
    r = client.delete(f"/quality-on-demand/v1/sessions/{sid}", headers=auth)
    assert r.status_code == 204
    assert core.pcf_deletes == ["/npcf-policyauthorization/v1/app-sessions/1/delete"]
    assert client.get(f"/quality-on-demand/v1/sessions/{sid}", headers=auth).status_code == 404
    ev = wait_for(lambda: [e for e in core.sink_events if e["data"]["qosStatus"] == "UNAVAILABLE"])[0]
    assert ev["data"]["statusInfo"] == "DELETE_REQUESTED"


def test_expiry(client, auth, core, store):
    sid = make_available(client, auth, core)
    store.db[COLLECTION].update_one({"_id": sid}, {"$set": {"expiresAt": datetime.now(timezone.utc) - timedelta(seconds=1)}})
    ev = wait_for(lambda: [e for e in core.sink_events if e["data"].get("statusInfo") == "DURATION_EXPIRED"])
    assert ev and core.pcf_deletes


def test_extend(client, auth, core):
    sid = make_available(client, auth, core)
    r = client.post(f"/quality-on-demand/v1/sessions/{sid}/extend", json={"requestedAdditionalDuration": 120}, headers=auth)
    assert r.status_code == 200 and r.json()["duration"] == 720
    r = client.post(f"/quality-on-demand/v1/sessions/{sid}/extend", json={"requestedAdditionalDuration": 10**6}, headers=auth)
    assert r.json()["duration"] == 86400      # capped at profile maxDuration


def test_extend_not_available(client, auth):
    sid = create(client, auth).json()["sessionId"]
    r = client.post(f"/quality-on-demand/v1/sessions/{sid}/extend", json={"requestedAdditionalDuration": 60}, headers=auth)
    assert r.status_code == 409 and r.json()["code"] == "QUALITY_ON_DEMAND.SESSION_EXTENSION_NOT_ALLOWED"


def test_verify_timeout_network_terminated(client, auth, core):
    sid = create(client, auth).json()["sessionId"]       # no flow ever appears
    ev = wait_for(lambda: [e for e in core.sink_events if e["data"].get("statusInfo") == "NETWORK_TERMINATED"], 5)
    assert ev[0]["data"]["sessionId"] == sid and core.pcf_deletes


def test_pcf_terminate_callback(client, auth, core):
    sid = make_available(client, auth, core)
    r = client.post(f"/_internal/pcf/qod/{sid}/terminate", json={"termCause": "PDU_SESSION_TERMINATION"})
    assert r.status_code == 204
    wait_for(lambda: [e for e in core.sink_events if e["data"].get("statusInfo") == "NETWORK_TERMINATED"])
    assert client.get(f"/quality-on-demand/v1/sessions/{sid}", headers=auth).status_code == 404


def test_retrieve_sessions(client, auth, core):
    sid = create(client, auth).json()["sessionId"]
    r = client.post("/quality-on-demand/v1/retrieve-sessions", json={"device": {"ipv4Address": {"privateAddress": "10.45.0.2"}}}, headers=auth)
    assert r.status_code == 200
    assert [s["sessionId"] for s in r.json()] == [sid]
    assert r.json()[0]["device"] == {"ipv4Address": {"privateAddress": "10.45.0.2"}}


@pytest.mark.parametrize("over,status,code", [
    ({"qosProfile": "QOS_X"}, 404, "NOT_FOUND"),
    ({"duration": 5}, 400, "QUALITY_ON_DEMAND.DURATION_OUT_OF_RANGE"),
    ({"applicationServer": {"ipv6Address": "2001:db8::/64"}}, 422, "SERVICE_NOT_APPLICABLE"),
    ({"applicationServer": {"ipAddresses": ["1.2.3.4"], "ipv4Address": "1.2.3.0/24"}}, 400, "INVALID_ARGUMENT"),
    ({"device": {"phoneNumber": "+99970000002"}, "applicationServer": {"ipv4Address": "999.1.1.1"}}, 400, "INVALID_ARGUMENT"),
    ({"sinkCredential": {"credentialType": "PRIVATE_KEY_JWT"}}, 422, "PRIVATE_KEY_JWT_NOT_CONFIGURED"),
    ({"device": {"phoneNumber": "+15550000000"}}, 404, "IDENTIFIER_NOT_FOUND"),
])
def test_create_errors(client, auth, over, status, code):
    r = create(client, auth, **over)
    assert (r.status_code, r.json()["code"]) == (status, code), r.text


@pytest.mark.parametrize("pcf_status,code", [(404, "SERVICE_NOT_APPLICABLE"), (403, "QUALITY_ON_DEMAND.QOS_PROFILE_NOT_APPLICABLE"), (500, "UNAVAILABLE")])
def test_pcf_errors_mapped(client, auth, core, pcf_status, code):
    core.pcf_status = pcf_status
    r = create(client, auth)
    assert r.json()["code"] == code, r.text


def test_https_sink_required_by_default(core, store, auth):
    with build_client(core, store) as c:
        tok = c.post("/oauth2/token", data={"grant_type": "client_credentials", "client_id": "demo-app",
                                            "client_secret": "s3cret"}).json()["access_token"]
        r = c.post("/quality-on-demand/v1/sessions", json=BODY, headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 400 and r.json()["code"] == "INVALID_SINK"


def test_session_isolated_per_client(client, auth):
    sid = create(client, auth).json()["sessionId"]
    other = client.post("/oauth2/token", data={"grant_type": "client_credentials", "client_id": "no-scope",
                                               "client_secret": "s3cret"}).json()["access_token"]
    r = client.get(f"/quality-on-demand/v1/sessions/{sid}", headers={"Authorization": f"Bearer {other}"})
    assert r.status_code == 403        # no read scope at all


def test_internal_callback_rejects_non_loopback(core, store):
    import httpx
    from fastapi.testclient import TestClient
    from camara_gw.app import create_app
    from camara_gw.core.info import InfoClient
    from camara_gw.core.sbi import SbiClient
    from tests.conftest import make_cfg
    cfg = make_cfg()
    t = httpx.MockTransport(core.handler)
    app = create_app(cfg, store=store, sbi=SbiClient(cfg.sbi, transport=t), info=InfoClient(cfg.info, transport=t))
    with TestClient(app, client=("192.168.1.50", 1234)) as c:
        assert c.post("/_internal/pcf/qod/x/terminate", json={}).status_code == 403
