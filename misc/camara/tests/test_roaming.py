"""Phase 4: Device Roaming Status + subscriptions + lab override."""

import time

import pytest

from camara_gw.roaming import status_for

BASE = "org.camaraproject.device-roaming-status-subscriptions.v0."
DEV = {"phoneNumber": "+99970000001"}


def wait_for(fn, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


def poll(client):
    r = client.portal.call(client.app.state.roaming.poll_once)
    for supi, prev, st in r:
        if prev is not None:
            for fn in client.app.state.roaming.listeners:
                client.portal.call(fn, supi, prev, st)
    return r


def override(client, auth, plmn):
    r = client.post("/_lab/roaming-override", json={"device": DEV, "servingPlmn": plmn}, headers=auth)
    assert r.status_code == 200, r.text


def retrieve(client, auth, device=DEV):
    return client.post("/device-roaming-status/v1/retrieve", json={"device": device}, headers=auth)


def subscribe(client, auth, name, **cfg):
    body = {"protocol": "HTTP", "sink": "http://sink.example/events", "types": [BASE + name],
            "config": {"subscriptionDetail": {"device": DEV}, **cfg}}
    r = client.post("/device-roaming-status-subscriptions/v0.9/subscriptions", json=body, headers=auth)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def events(core, name):
    return [e for e in core.sink_events if e["type"] == BASE + name]


def test_status_for():
    assert status_for("99970", ["99970"]) == {"roaming": False, "servingPlmn": "99970"}
    assert status_for("20801", ["99970"]) == {"roaming": True, "servingPlmn": "20801", "countryCode": 208, "countryName": ["FR"]}
    assert status_for("00101", ["99970"])["countryName"] == []


def test_retrieve_home(client, auth):
    r = retrieve(client, auth)
    assert r.status_code == 200
    j = r.json()
    assert j["roaming"] is False and j["device"] == DEV and j["lastStatusTime"].endswith("Z")
    assert "countryCode" not in j


def test_retrieve_with_override(client, auth):
    override(client, auth, "26201")
    j = retrieve(client, auth).json()
    assert (j["roaming"], j["countryCode"], j["countryName"]) == (True, 262, ["DE"])
    override(client, auth, None)
    assert retrieve(client, auth).json()["roaming"] is False


def test_retrieve_last_known_when_ue_gone(client, auth, core):
    poll(client)                                         # records state for UE1
    core.ue_info = {"items": [], "pager": {"page": 0, "page_size": 100, "count": 0}}
    j = retrieve(client, auth).json()
    assert j["roaming"] is False and j["lastStatusTime"]


def test_retrieve_never_seen(client, auth):
    r = retrieve(client, auth, {"phoneNumber": "+99970000002"})   # UE2 has a PDU session but no AMF entry
    assert r.status_code == 422 and r.json()["code"] == "SERVICE_NOT_APPLICABLE"


def test_retrieve_errors(client, auth):
    assert retrieve(client, auth, {"phoneNumber": "+15550000000"}).json()["code"] == "IDENTIFIER_NOT_FOUND"
    r = client.post("/device-roaming-status/v1/retrieve", json={}, headers=auth)
    assert r.json()["code"] == "MISSING_IDENTIFIER"


def test_events_on_change_country_off(client, auth, core):
    poll(client)                                         # baseline (home)
    subscribe(client, auth, "roaming-status")
    subscribe(client, auth, "roaming-on")
    subscribe(client, auth, "roaming-off")
    subscribe(client, auth, "roaming-change-country")

    override(client, auth, "20801"); poll(client)        # -> roaming FR
    on = wait_for(lambda: events(core, "roaming-on"))[0]
    st = wait_for(lambda: events(core, "roaming-status"))[0]
    assert on["data"]["device"] == DEV and "subscriptionId" in on["data"]
    assert st["data"]["roaming"] is True and st["data"]["countryCode"] == 208 and st["data"]["countryName"] == ["FR"]

    override(client, auth, "26201"); poll(client)        # FR -> DE while roaming
    cc = wait_for(lambda: events(core, "roaming-change-country"))[0]
    assert (cc["data"]["countryCode"], cc["data"]["countryName"]) == (262, ["DE"])
    assert len(events(core, "roaming-on")) == 1          # no duplicate roaming-on

    override(client, auth, None); poll(client)           # back home
    wait_for(lambda: events(core, "roaming-off"))
    assert wait_for(lambda: len(events(core, "roaming-status")) == 2)


def test_no_event_without_change(client, auth, core):
    poll(client)
    subscribe(client, auth, "roaming-status")
    poll(client); poll(client)
    time.sleep(0.2)
    assert events(core, "roaming-status") == []


def test_initial_event(client, auth, core):
    override(client, auth, "23415")
    subscribe(client, auth, "roaming-on", initialEvent=True)
    ev = wait_for(lambda: events(core, "roaming-on"))[0]
    assert ev["data"]["device"] == DEV
    subscribe(client, auth, "roaming-off", initialEvent=True)    # not roaming-off now -> no event
    time.sleep(0.2)
    assert events(core, "roaming-off") == []


def test_multievent_not_supported(client, auth):
    body = {"protocol": "HTTP", "sink": "http://sink.example/events",
            "types": [BASE + "roaming-on", BASE + "roaming-off"], "config": {"subscriptionDetail": {"device": DEV}}}
    r = client.post("/device-roaming-status-subscriptions/v0.9/subscriptions", json=body, headers=auth)
    assert r.status_code == 422 and r.json()["code"] == "MULTIEVENT_SUBSCRIPTION_NOT_SUPPORTED"


def test_subscription_unknown_device(client, auth):
    body = {"protocol": "HTTP", "sink": "http://sink.example/events", "types": [BASE + "roaming-on"],
            "config": {"subscriptionDetail": {"device": {"phoneNumber": "+15550000000"}}}}
    r = client.post("/device-roaming-status-subscriptions/v0.9/subscriptions", json=body, headers=auth)
    assert r.status_code == 404 and r.json()["code"] == "IDENTIFIER_NOT_FOUND"


def test_override_requires_lab_scope(client):
    tok = client.post("/oauth2/token", data={"grant_type": "client_credentials", "client_id": "no-scope",
                                             "client_secret": "s3cret"}).json()["access_token"]
    r = client.post("/_lab/roaming-override", json={"device": DEV, "servingPlmn": "20801"},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403
