"""Regression tests for gaps found against the CAMARA test definitions (Phase 6 review)."""

import pytest

from camara_gw.auth.tokens import issue_token

DEV = {"phoneNumber": "+99970000001"}
QOD = {"applicationServer": {"ipv4Address": "198.51.100.10"}, "qosProfile": "QOS_E", "duration": 600}
B = "org.camaraproject.device-roaming-status-subscriptions.v0."


def three_legged(client, *scopes, sub="imsi-999700000000001"):
    tok = issue_token(client.app.state.config, "demo-app", list(scopes), sub=sub, extra={"amr": ["network"]})
    return {"Authorization": f"Bearer {tok['access_token']}"}


# ── device from a 3-legged token ─────────────────────────────────────────────
def test_three_legged_device_from_token(client):
    h = three_legged(client, "device-roaming-status:read", "quality-on-demand:sessions:create", "sim-swap:check",
                     "qos-profiles:read")
    r = client.post("/device-roaming-status/v1/retrieve", json={}, headers=h)
    assert r.status_code == 200 and "device" not in r.json()
    r = client.post("/quality-on-demand/v1/sessions", json=QOD, headers=h)
    assert r.status_code == 201 and "device" not in r.json()
    client.app.state.swaps.poll_once()
    assert client.post("/sim-swap/v2/check", json={}, headers=h).json() == {"swapped": False}
    assert client.post("/qos-profiles/v1/retrieve-qos-profiles", json={}, headers=h).status_code == 200


def test_three_legged_plus_device_unnecessary(client):
    h = three_legged(client, "device-roaming-status:read", "sim-swap:check")
    assert client.post("/device-roaming-status/v1/retrieve", json={"device": DEV}, headers=h).json()["code"] == "UNNECESSARY_IDENTIFIER"
    assert client.post("/sim-swap/v2/check", json={"phoneNumber": "+99970000001"}, headers=h).json()["code"] == "UNNECESSARY_IDENTIFIER"


def test_two_legged_without_device_missing(client, auth):
    assert client.post("/device-roaming-status/v1/retrieve", json={}, headers=auth).json()["code"] == "MISSING_IDENTIFIER"
    assert client.post("/quality-on-demand/v1/sessions", json=QOD, headers=auth).json()["code"] == "MISSING_IDENTIFIER"


@pytest.mark.parametrize("path,body", [
    ("/device-roaming-status/v1/retrieve", {"device": {}}),
    ("/qos-profiles/v1/retrieve-qos-profiles", {"device": {}}),
    ("/quality-on-demand/v1/sessions", {**QOD, "device": {}}),
])
def test_empty_device_object_is_400(client, auth, path, body):
    r = client.post(path, json=body, headers=auth)
    assert (r.status_code, r.json()["code"]) == (400, "INVALID_ARGUMENT")


# ── QoD specifics ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("over,status,code", [
    ({"applicationServerPorts": {"ports": [70000]}}, 400, "OUT_OF_RANGE"),
    ({"devicePorts": {"ranges": [{"from": 6000, "to": 5000}]}}, 400, "OUT_OF_RANGE"),
    ({"applicationServer": {"ipAddresses": ["1.2.3.4", "1.2.3.4"]}}, 400, "INVALID_ARGUMENT"),
    ({"applicationServer": {"ipAddresses": [f"10.0.0.{i}" for i in range(17)]}}, 400, "INVALID_ARGUMENT"),
    ({"applicationServer": {"ipAddresses": ["10.0.0.0/24"]}}, 400, "INVALID_ARGUMENT"),
    ({"sink": "http://sink.example/e", "sinkCredential": {"credentialType": "PLAIN"}}, 400, "INVALID_ARGUMENT"),
    ({"sink": "http://sink.example/e", "sinkCredential": {"credentialType": "ACCESSTOKEN", "accessToken": "x",
      "accessTokenExpiresUtc": "2030-01-01T00:00:00Z", "accessTokenType": "mac"}}, 400, "INVALID_TOKEN"),
])
def test_qod_create_conformance(client, auth, over, status, code):
    r = client.post("/quality-on-demand/v1/sessions", json={**QOD, "device": DEV, **over}, headers=auth)
    assert (r.status_code, r.json()["code"]) == (status, code), r.text


@pytest.mark.parametrize("method,path", [("get", "/quality-on-demand/v1/sessions/not-a-uuid"),
                                         ("delete", "/quality-on-demand/v1/sessions/not-a-uuid")])
def test_invalid_session_id_400(client, auth, method, path):
    r = getattr(client, method)(path, headers=auth)
    assert r.status_code == 400 and r.json()["code"] == "INVALID_ARGUMENT"


def test_qos_profile_name_pattern(client, auth):
    assert client.get("/qos-profiles/v1/qos-profiles/a", headers=auth).status_code == 400          # too short
    assert client.get("/qos-profiles/v1/qos-profiles/bad%20name", headers=auth).status_code == 400


# ── swap specifics ───────────────────────────────────────────────────────────
def test_max_age_above_limit_out_of_range(client, auth):
    r = client.post("/sim-swap/v2/check", json={"phoneNumber": "+99970000001", "maxAge": 2401}, headers=auth)
    assert (r.status_code, r.json()["code"]) == (400, "OUT_OF_RANGE")
    r = client.post("/sim-swap/v2/check", json={"phoneNumber": "+99970000001", "maxAge": 0}, headers=auth)
    assert (r.status_code, r.json()["code"]) == (400, "INVALID_ARGUMENT")


def test_device_swap_never_connected(client, auth, store):
    store.subscribers.insert_one({"imsi": "999700000000009", "msisdn": ["99970000009"], "imeisv": []})
    r = client.post("/device-swap/v1/check", json={"phoneNumber": "+99970000009"}, headers=auth)
    assert (r.status_code, r.json()["code"]) == (422, "SERVICE_NOT_APPLICABLE")


# ── subscriptions specifics ──────────────────────────────────────────────────
def test_subscription_mismatch(client):
    tok = issue_token(client.app.state.config, "demo-app",
                      [f"device-roaming-status-subscriptions:{B}roaming-on:create"])
    body = {"protocol": "HTTP", "sink": "http://sink.example/e", "types": [B + "roaming-off"],
            "config": {"subscriptionDetail": {"device": DEV}}}
    r = client.post("/device-roaming-status-subscriptions/v0.9/subscriptions", json=body,
                    headers={"Authorization": f"Bearer {tok['access_token']}"})
    assert (r.status_code, r.json()["code"]) == (403, "SUBSCRIPTION_MISMATCH")


@pytest.mark.parametrize("cred,code", [
    ({"credentialType": "PLAIN"}, "INVALID_CREDENTIAL"),
    ({"credentialType": "ACCESSTOKEN", "accessToken": "x", "accessTokenExpiresUtc": "2030-01-01T00:00:00Z",
      "accessTokenType": "mac"}, "INVALID_TOKEN"),
])
def test_subscription_sink_credential(client, auth, cred, code):
    body = {"protocol": "HTTP", "sink": "http://sink.example/e", "sinkCredential": cred, "types": [B + "roaming-on"],
            "config": {"subscriptionDetail": {"device": DEV}}}
    r = client.post("/device-roaming-status-subscriptions/v0.9/subscriptions", json=body, headers=auth)
    assert (r.status_code, r.json()["code"]) == (400, code)


def test_sink_credential_returned_without_token(client, auth):
    cred = {"credentialType": "ACCESSTOKEN", "accessToken": "secret", "accessTokenExpiresUtc": "2030-01-01T00:00:00Z",
            "accessTokenType": "bearer"}
    body = {"protocol": "HTTP", "sink": "http://sink.example/e", "sinkCredential": cred, "types": [B + "roaming-on"],
            "config": {"subscriptionDetail": {"device": DEV}}}
    sid = client.post("/device-roaming-status-subscriptions/v0.9/subscriptions", json=body, headers=auth).json()["id"]
    got = client.get(f"/device-roaming-status-subscriptions/v0.9/subscriptions/{sid}", headers=auth).json()
    assert got["sinkCredential"] == {"credentialType": "ACCESSTOKEN", "accessTokenExpiresUtc": "2030-01-01T00:00:00Z",
                                     "accessTokenType": "bearer"}


@pytest.mark.parametrize("q", ["page=0", "perPage=101", "page=x"])
def test_subscription_pagination_invalid(client, auth, q):
    r = client.get(f"/device-roaming-status-subscriptions/v0.9/subscriptions?{q}", headers=auth)
    assert (r.status_code, r.json()["code"]) == (400, "INVALID_ARGUMENT")


def test_app_profile_invalid_uuid(client, auth):
    r = client.get("/application-profiles/v0.6/application-profiles/not-a-uuid", headers=auth)
    assert (r.status_code, r.json()["code"]) == (400, "INVALID_ARGUMENT")


def test_rate_limit_and_quota(core, store):
    from tests.conftest import build_client
    with build_client(core, store, swap={"enabled": False}, roaming={"enabled": False},
                      rate_limit={"per_minute": 3, "daily_quota": 0}) as c:
        h = {"Authorization": "Bearer " + issue_token(c.app.state.config, "demo-app", ["qos-profiles:read"])["access_token"]}
        codes = [c.get("/qos-profiles/v1/qos-profiles/QOS_E", headers=h).status_code for _ in range(4)]
        assert codes == [200, 200, 200, 429]
        assert c.get("/qos-profiles/v1/qos-profiles/QOS_E", headers=h).json()["code"] == "TOO_MANY_REQUESTS"
    with build_client(core, store, swap={"enabled": False}, roaming={"enabled": False},
                      rate_limit={"per_minute": 100, "daily_quota": 2}) as c:
        h = {"Authorization": "Bearer " + issue_token(c.app.state.config, "demo-app", ["qos-profiles:read"])["access_token"]}
        r = [c.get("/qos-profiles/v1/qos-profiles/QOS_E", headers=h) for _ in range(3)][-1]
        assert (r.status_code, r.json()["code"]) == (429, "QUOTA_EXCEEDED")
