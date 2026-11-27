"""Phase 0: errors/x-correlator, auth, health, resolver."""

import base64

import pytest


# ── x-correlator & error model ───────────────────────────────────────────────
def test_correlator_echoed_and_generated(client):
    r = client.get("/health", headers={"x-correlator": "abc-123"})
    assert r.headers["x-correlator"] == "abc-123"
    assert client.get("/health").headers["x-correlator"]


def test_bad_correlator_rejected(client):
    r = client.get("/health", headers={"x-correlator": "bad correlator!"})
    assert r.status_code == 400 and r.json()["code"] == "INVALID_ARGUMENT"


def test_unknown_route_camara_error(client):
    r = client.get("/nope")
    assert r.status_code == 404 and set(r.json()) == {"status", "code", "message"}


# ── auth ─────────────────────────────────────────────────────────────────────
def test_token_basic_auth(client):
    basic = base64.b64encode(b"demo-app:s3cret").decode()
    r = client.post("/oauth2/token", data={"grant_type": "client_credentials"},
                    headers={"Authorization": f"Basic {basic}"})
    assert r.status_code == 200 and r.json()["token_type"] == "Bearer"


@pytest.mark.parametrize("data,status,code", [
    ({"grant_type": "client_credentials", "client_id": "demo-app", "client_secret": "wrong"}, 401, "invalid_client"),
    ({"grant_type": "password", "client_id": "demo-app", "client_secret": "s3cret"}, 400, "unsupported_grant_type"),
    ({"grant_type": "client_credentials", "client_id": "demo-app", "client_secret": "s3cret", "scope": "qod:admin"},
     400, "invalid_scope"),
])
def test_token_errors(client, data, status, code):
    r = client.post("/oauth2/token", data=data)
    assert r.status_code == status and r.json()["code"] == code


def test_missing_and_bad_bearer(client):
    body = {"device": {"phoneNumber": "+99970000001"}}
    assert client.post("/_lab/resolve", json=body).json()["code"] == "UNAUTHENTICATED"
    r = client.post("/_lab/resolve", json=body, headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401


def test_missing_scope(client):
    tok = client.post("/oauth2/token", data={"grant_type": "client_credentials", "client_id": "no-scope",
                                             "client_secret": "s3cret"}).json()["access_token"]
    r = client.post("/_lab/resolve", json={"device": {"phoneNumber": "+99970000001"}},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403 and r.json()["code"] == "PERMISSION_DENIED"


# ── health ───────────────────────────────────────────────────────────────────
def test_health_up(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "UP"


def test_health_down_when_nf_missing(client, core):
    core.nrf_down.add("PCF")
    r = client.get("/health")
    assert r.status_code == 503
    assert r.json()["checks"]["nrf:PCF"]["ok"] is False


# ── resolver ─────────────────────────────────────────────────────────────────
def _resolve(client, auth, device):
    return client.post("/_lab/resolve", json={"device": device}, headers=auth)


def test_resolve_by_phone(client, auth):
    r = _resolve(client, auth, {"phoneNumber": "+99970000001"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["supi"] == "imsi-999700000000001" and j["ipv4Addresses"] == ["10.45.0.2"]
    assert j["matched_by"] == ["phoneNumber"]


def test_resolve_by_ipv4_fills_phone(client, auth):
    j = _resolve(client, auth, {"ipv4Address": {"privateAddress": "10.45.0.3"}}).json()
    assert j["supi"] == "imsi-999700000000002" and j["phone_number"] == "+99970000002"


def test_resolve_by_ipv6_prefix(client, auth):
    j = _resolve(client, auth, {"ipv6Address": "2001:db8:cafe::abcd"}).json()
    assert j["supi"] == "imsi-999700000000001"


def test_resolve_multi_identifier_consistent(client, auth):
    # all identifiers point at UE1 -> resolved, only the phone echoed back
    r = _resolve(client, auth, {"phoneNumber": "+99970000001", "ipv4Address": {"privateAddress": "10.45.0.2"},
                                "ipv6Address": "2001:db8:cafe::2"})
    j = r.json()
    assert r.status_code == 200, r.text
    assert j["matched_by"] == ["phoneNumber", "ipv4Address", "ipv6Address"]
    assert j["supi"] == "imsi-999700000000001" and j["device_response"] == {"phoneNumber": "+99970000001"}


@pytest.mark.parametrize("device", [
    {"phoneNumber": "+99970000001", "ipv4Address": {"privateAddress": "10.45.0.3"}},   # UE1 phone, UE2 IP
    {"phoneNumber": "+99970000002", "ipv6Address": "2001:db8:cafe::2"},                # UE2 phone, UE1 IPv6
    {"ipv4Address": {"privateAddress": "10.45.0.3"}, "ipv6Address": "2001:db8:cafe::2"},
])
def test_resolve_multi_identifier_mismatch(client, auth, device):
    r = _resolve(client, auth, device)
    assert (r.status_code, r.json()["code"]) == (422, "IDENTIFIER_MISMATCH"), r.text


def test_resolve_multi_identifier_unknown_ip_is_not_found(client, auth):
    # a SIM that left the network: phone still resolves, but its claimed IP has no session
    r = _resolve(client, auth, {"phoneNumber": "+99970000001", "ipv4Address": {"privateAddress": "10.45.9.9"}})
    assert (r.status_code, r.json()["code"]) == (404, "IDENTIFIER_NOT_FOUND")


def test_match_identifier_wrong_sim(client, auth):
    # wrong-SIM case end to end: claimed phone UE1, UE IP held by UE2 -> 422, not a match
    body = {"device": {"phoneNumber": "+99970000001", "ipv4Address": {"privateAddress": "10.45.0.3"}},
            "imeisv": "4370816125816151"}
    r = client.post("/device-identifier/v0.4/match-identifier", json=body, headers=auth)
    assert (r.status_code, r.json()["code"]) == (422, "IDENTIFIER_MISMATCH"), r.text
    body["device"]["ipv4Address"] = {"privateAddress": "10.45.0.2"}
    r = client.post("/device-identifier/v0.4/match-identifier", json=body, headers=auth)
    assert r.status_code == 200 and r.json()["match"] is True, r.text


@pytest.mark.parametrize("device,status,code", [
    ({"phoneNumber": "+99970000009"}, 404, "IDENTIFIER_NOT_FOUND"),
    ({"ipv4Address": {"privateAddress": "10.45.9.9"}}, 404, "IDENTIFIER_NOT_FOUND"),
    ({"networkAccessIdentifier": "user@example.com"}, 422, "UNSUPPORTED_IDENTIFIER"),
    ({}, 400, "INVALID_ARGUMENT"),                                      # empty device object (minProperties 1)
    ({"phoneNumber": "99970000001"}, 400, "INVALID_ARGUMENT"),           # not E.164
])
def test_resolve_errors(client, auth, device, status, code):
    r = _resolve(client, auth, device)
    assert (r.status_code, r.json()["code"]) == (status, code), r.text


def test_resolve_missing_device(client, auth):
    r = client.post("/_lab/resolve", json={}, headers=auth)
    assert r.json()["code"] == "MISSING_IDENTIFIER"


def test_resolve_ambiguous_phone(client, auth, store):
    store.subscribers.insert_one({"imsi": "999700000000003", "msisdn": ["99970000001"]})
    r = _resolve(client, auth, {"phoneNumber": "+99970000001"})
    assert r.status_code == 422 and r.json()["code"] == "SERVICE_NOT_APPLICABLE"


def test_info_unavailable_maps_to_503(store):
    import httpx
    from fastapi.testclient import TestClient
    from camara_gw.app import create_app
    from camara_gw.core.info import InfoClient
    from camara_gw.core.sbi import SbiClient
    from tests.conftest import make_cfg

    def down(request):
        raise httpx.ConnectError("connection refused")

    cfg = make_cfg()
    t = httpx.MockTransport(down)
    app = create_app(cfg, store=store, sbi=SbiClient(cfg.sbi, transport=t), info=InfoClient(cfg.info, transport=t))
    with TestClient(app) as c:
        tok = c.post("/oauth2/token", data={"grant_type": "client_credentials", "client_id": "demo-app",
                                            "client_secret": "s3cret"}).json()["access_token"]
        r = c.post("/_lab/resolve", json={"device": {"phoneNumber": "+99970000001"}},
                   headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 503 and r.json()["code"] == "UNAVAILABLE"
        assert c.get("/health").status_code == 503
