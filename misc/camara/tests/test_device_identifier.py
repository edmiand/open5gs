"""Device Identifier v0.4.0."""

import pytest

from camara_gw.apis.device_identifier import luhn_check_digit, split_imeisv
from camara_gw.auth.tokens import issue_token

DEV = {"phoneNumber": "+99970000001"}           # UE1: AMF pei imeisv-4370816125816151
IMEI1, IMEISV1, TAC1 = "437081612581614", "4370816125816151", "43708161"


def post(client, auth, op, body):
    return client.post(f"/device-identifier/v0.4/{op}", json=body, headers=auth)


@pytest.mark.parametrize("imei", ["490154203237518", "356938035643809"])
def test_luhn(imei):
    assert luhn_check_digit(imei[:14]) == imei[14]


def test_split():
    assert split_imeisv("imeisv-4370816125816151") == {"imei": IMEI1, "tac": TAC1, "imeisv": IMEISV1}
    assert split_imeisv(["4370816125816151"])["imei"] == IMEI1
    assert split_imeisv("bogus") is None and split_imeisv([]) is None


def test_retrieve_identifier_live_from_amf(client, auth):
    r = post(client, auth, "retrieve-identifier", {"device": DEV})
    assert r.status_code == 200, r.text
    j = r.json()
    assert (j["imei"], j["imeisv"], j["tac"]) == (IMEI1, IMEISV1, TAC1)
    assert j["manufacturer"] == "UERANSIM" and j["device"] == DEV and j["lastChecked"].endswith("Z")


def test_retrieve_identifier_falls_back_to_db(client, auth):
    # UE2 isn't in AMF /ue-info -> last IMEISV stored by the UDR, lastChecked null
    j = post(client, auth, "retrieve-identifier", {"device": {"phoneNumber": "+99970000002"}}).json()
    assert j["imeisv"] == "4370816125816152" and j["lastChecked"] is None


def test_retrieve_type_and_ppid(client, auth):
    t = post(client, auth, "retrieve-type", {"device": DEV}).json()
    assert t["tac"] == TAC1 and t["model"] == "nr-ue (default)" and "imei" not in t
    p1 = post(client, auth, "retrieve-ppid", {"device": DEV}).json()["ppid"]
    p1b = post(client, auth, "retrieve-ppid", {"device": DEV}).json()["ppid"]
    other = {"Authorization": "Bearer " + issue_token(client.app.state.config, "other-app",
                                                      ["device-identifier:retrieve-ppid"])["access_token"]}
    p2 = post(client, other, "retrieve-ppid", {"device": DEV}).json()["ppid"]
    assert len(p1) == 64 and p1 == p1b and p1 != p2            # stable per client, pairwise across clients


@pytest.mark.parametrize("field,value,match", [("imei", IMEI1, True), ("imei", "490154203237518", False),
                                               ("imeisv", IMEISV1, True), ("imeisv", "4370816125816199", False),
                                               ("tac", TAC1, True), ("tac", "35288200", False)])
def test_match_identifier(client, auth, field, value, match):
    j = post(client, auth, "match-identifier", {"device": DEV, field: value}).json()
    assert j["match"] is match and "lastChecked" in j


@pytest.mark.parametrize("body,status,code", [
    ({"device": DEV}, 400, "INVALID_ARGUMENT"),                                     # no identifier
    ({"device": DEV, "imei": IMEI1, "tac": TAC1}, 400, "INVALID_ARGUMENT"),          # two identifiers
    ({"device": DEV, "imei": "12345"}, 400, "INVALID_ARGUMENT"),                     # bad format
    ({"imei": IMEI1}, 422, "MISSING_IDENTIFIER"),                                    # 2-legged, no device
    ({"device": {"phoneNumber": "+15550000000"}, "imei": IMEI1}, 404, "IDENTIFIER_NOT_FOUND"),
])
def test_match_errors(client, auth, body, status, code):
    r = post(client, auth, "match-identifier", body)
    assert (r.status_code, r.json()["code"]) == (status, code), r.text


def test_three_legged(client):
    h = {"Authorization": "Bearer " + issue_token(client.app.state.config, "demo-app",
         ["device-identifier:match-identifier", "device-identifier:retrieve-identifier"],
         sub="imsi-999700000000001", extra={"amr": ["network"]})["access_token"]}
    j = post(client, h, "match-identifier", {"imei": IMEI1}).json()
    assert j["match"] is True and "device" not in j
    r = post(client, h, "match-identifier", {"device": DEV, "imei": IMEI1})
    assert r.json()["code"] == "UNNECESSARY_IDENTIFIER"


def test_never_registered_device(client, auth, store):
    store.subscribers.insert_one({"imsi": "999700000000009", "msisdn": ["99970000009"], "imeisv": []})
    r = post(client, auth, "retrieve-identifier", {"device": {"phoneNumber": "+99970000009"}})
    assert (r.status_code, r.json()["code"]) == (422, "SERVICE_NOT_APPLICABLE")
