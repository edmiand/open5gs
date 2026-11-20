"""Phase 3: network-based authorization-code flow + Number Verification."""

import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import pytest

from tests.conftest import build_client

REDIR = "https://demo-app.example/callback"
SCOPE_V = "openid number-verification:verify"
SCOPE_R = "openid number-verification:device-phone-number:read"


@pytest.fixture
def ue(core, store):
    """Client whose source IP is the UE's PDU session address (10.45.0.2 -> imsi ...001)."""
    with build_client(core, store, client_addr=("10.45.0.2", 40000), notify={"allow_http_sink": True},
                      swap={"enabled": False}) as c:
        yield c


def authorize(c, scope=SCOPE_V, **extra):
    params = {"response_type": "code", "client_id": "demo-app", "redirect_uri": REDIR, "scope": scope,
              "state": "st-1", **extra}
    r = c.get("/oauth2/authorize", params=params)
    assert r.status_code == 302, r.text
    return parse_qs(urlparse(r.headers["location"]).query)


def exchange(c, code, **extra):
    return c.post("/oauth2/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIR,
                                         "client_id": "demo-app", "client_secret": "s3cret", **extra})


def nv_token(c, scope=SCOPE_V):
    q = authorize(c, scope)
    r = exchange(c, q["code"][0])
    assert r.status_code == 200, r.text
    return r.json()


def bearer(tok):
    return {"Authorization": f"Bearer {tok['access_token']}"}


def test_authorize_from_ue_issues_code(ue):
    q = authorize(ue)
    assert q["state"] == ["st-1"] and q["code"][0]


def test_token_claims(ue):
    import jwt
    tok = nv_token(ue)
    assert tok["expires_in"] <= 300 and "refresh_token" not in tok
    claims = jwt.decode(tok["access_token"], options={"verify_signature": False})
    assert claims["sub"] == "imsi-999700000000001" and claims["amr"] == ["network"]
    idt = jwt.decode(tok["id_token"], options={"verify_signature": False})
    assert idt["aud"] == "demo-app" and idt["sub"] == "imsi-999700000000001"


def test_verify_true_false_and_hashed(ue):
    assert ue.post("/number-verification/v2/verify", json={"phoneNumber": "+99970000001"},
                   headers=bearer(nv_token(ue))).json() == {"devicePhoneNumberVerified": True}
    assert ue.post("/number-verification/v2/verify", json={"phoneNumber": "+99970000002"},
                   headers=bearer(nv_token(ue))).json() == {"devicePhoneNumberVerified": False}
    h = hashlib.sha256(b"+99970000001").hexdigest()
    assert ue.post("/number-verification/v2/verify", json={"hashedPhoneNumber": h},
                   headers=bearer(nv_token(ue))).json() == {"devicePhoneNumberVerified": True}


def test_device_phone_number(ue):
    r = ue.get("/number-verification/v2/device-phone-number", headers=bearer(nv_token(ue, SCOPE_R)))
    assert r.json() == {"devicePhoneNumber": "+99970000001"}


def test_token_single_use(ue):
    tok = nv_token(ue)
    assert ue.post("/number-verification/v2/verify", json={"phoneNumber": "+99970000001"}, headers=bearer(tok)).status_code == 200
    r = ue.post("/number-verification/v2/verify", json={"phoneNumber": "+99970000001"}, headers=bearer(tok))
    assert r.status_code == 401 and r.json()["code"] == "UNAUTHENTICATED"


def test_code_single_use_and_client_bound(ue):
    code = authorize(ue)["code"][0]
    assert exchange(ue, code).status_code == 200
    assert exchange(ue, code).json()["code"] == "invalid_grant"
    code2 = authorize(ue)["code"][0]
    assert exchange(ue, code2, redirect_uri="https://evil.example/cb").json()["code"] == "invalid_grant"


def test_pkce(ue):
    verifier = "v" * 50
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    code = authorize(ue, code_challenge=challenge, code_challenge_method="S256")["code"][0]
    assert exchange(ue, code, code_verifier="wrong").json()["code"] == "invalid_grant"
    code = authorize(ue, code_challenge=challenge, code_challenge_method="S256")["code"][0]
    assert exchange(ue, code, code_verifier=verifier).status_code == 200


def test_off_network_access_denied(client):
    # source 127.0.0.1 is not a UE address
    q = authorize(client)
    assert q["error"] == ["access_denied"] and q["state"] == ["st-1"] and "code" not in q


def test_unknown_ue_ip_access_denied(core, store):
    with build_client(core, store, client_addr=("10.45.9.9", 1), swap={"enabled": False}) as c:
        assert authorize(c)["error"] == ["access_denied"]


def test_unregistered_redirect_not_followed(ue):
    r = ue.get("/oauth2/authorize", params={"response_type": "code", "client_id": "demo-app",
                                            "redirect_uri": "https://evil.example/cb", "scope": SCOPE_V})
    assert r.status_code == 400 and "location" not in r.headers


def test_missing_openid_scope(ue):
    assert authorize(ue, scope="number-verification:verify")["error"] == ["invalid_scope"]


def test_two_legged_token_cannot_get_nv_scopes(client):
    r = client.post("/oauth2/token", data={"grant_type": "client_credentials", "client_id": "demo-app",
                                           "client_secret": "s3cret", "scope": "number-verification:verify"})
    assert r.status_code == 400 and r.json()["code"] == "invalid_scope"


def test_verify_without_network_auth(client):
    from camara_gw.auth.tokens import issue_token
    tok = issue_token(client.app.state.config, "demo-app", ["number-verification:verify"])   # forged 2-legged
    r = client.post("/number-verification/v2/verify", json={"phoneNumber": "+99970000001"}, headers=bearer(tok))
    assert r.status_code == 403 and r.json()["code"] == "NUMBER_VERIFICATION.USER_NOT_AUTHENTICATED_BY_MOBILE_NETWORK"


@pytest.mark.parametrize("body", [{}, {"phoneNumber": "+99970000001", "hashedPhoneNumber": "a" * 64},
                                  {"hashedPhoneNumber": "xyz"}, {"phoneNumber": "99970000001"}])
def test_verify_bad_body(ue, body):
    r = ue.post("/number-verification/v2/verify", json=body, headers=bearer(nv_token(ue)))
    assert r.status_code == 400 and r.json()["code"] == "INVALID_ARGUMENT"


def test_discovery(client):
    j = client.get("/.well-known/openid-configuration").json()
    assert j["authorization_endpoint"].endswith("/oauth2/authorize") and "S256" in j["code_challenge_methods_supported"]
