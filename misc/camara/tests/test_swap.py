"""Phase 2: SIM swap, device swap, SIM swap subscriptions."""

import time
from datetime import datetime, timedelta, timezone

import pytest

from camara_gw.swap_history import EVENTS, imei_of

SUB_TYPE = "org.camaraproject.sim-swap-subscriptions.v0.swapped"


def wait_for(fn, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


def poll(client):
    return client.app.state.swaps.poll_once()


def move_msisdn(store, msisdn, from_imsi, to_imsi):
    store.subscribers.update_one({"imsi": from_imsi}, {"$pull": {"msisdn": msisdn}})
    store.subscribers.update_one({"imsi": to_imsi}, {"$push": {"msisdn": msisdn}})


# ── helpers ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,imei", [("4370816125816151", "43708161258161"), (["4370816125816151"], "43708161258161"),
                                      ("imeisv-4370816125816151", "43708161258161"), (None, None), ("12", None)])
def test_imei_of(raw, imei):
    assert imei_of(raw) == imei


# ── SIM swap ─────────────────────────────────────────────────────────────────
def test_no_history_returns_null_and_monitored_period(client, auth):
    poll(client)                                     # baseline
    r = client.post("/sim-swap/v2/retrieve-date", json={"phoneNumber": "+99970000001"}, headers=auth)
    assert r.status_code == 200 and r.json() == {"latestSimChange": None, "monitoredPeriod": 1}
    r = client.post("/sim-swap/v2/check", json={"phoneNumber": "+99970000001"}, headers=auth)
    assert r.json() == {"swapped": False}


def test_sim_swap_detected(client, auth, store):
    poll(client)
    move_msisdn(store, "99970000001", "999700000000001", "999700000000002")
    evs = poll(client)
    assert [(e["type"], e["old"], e["new"]) for e in evs] == [("SIM_SWAP", "999700000000001", "999700000000002")]
    r = client.post("/sim-swap/v2/retrieve-date", json={"phoneNumber": "+99970000001"}, headers=auth)
    assert r.json()["latestSimChange"].endswith("Z") and "monitoredPeriod" not in r.json()
    assert client.post("/sim-swap/v2/check", json={"phoneNumber": "+99970000001", "maxAge": 1}, headers=auth).json() == {"swapped": True}


def test_sim_swap_remove_then_add_is_one_swap(client, store):
    poll(client)
    store.subscribers.update_one({"imsi": "999700000000001"}, {"$pull": {"msisdn": "99970000001"}})
    assert poll(client) == []                        # number temporarily unassigned
    store.subscribers.update_one({"imsi": "999700000000002"}, {"$push": {"msisdn": "99970000001"}})
    assert [e["type"] for e in poll(client)] == ["SIM_SWAP"]


def test_check_respects_max_age(client, auth, store):
    poll(client)
    move_msisdn(store, "99970000001", "999700000000001", "999700000000002")
    poll(client)
    store.db[EVENTS].update_many({}, {"$set": {"ts": datetime.now(timezone.utc) - timedelta(hours=5)}})
    assert client.post("/sim-swap/v2/check", json={"phoneNumber": "+99970000001", "maxAge": 4}, headers=auth).json() == {"swapped": False}
    assert client.post("/sim-swap/v2/check", json={"phoneNumber": "+99970000001", "maxAge": 6}, headers=auth).json() == {"swapped": True}


# ── device swap ──────────────────────────────────────────────────────────────
def test_device_swap_detected_but_not_sv_change(client, auth, store):
    poll(client)
    store.subscribers.update_one({"imsi": "999700000000001"}, {"$set": {"imeisv": "4370816125816199"}})   # SV only
    assert poll(client) == []
    store.subscribers.update_one({"imsi": "999700000000001"}, {"$set": {"imeisv": "3528820000000101"}})   # new device
    assert [e["type"] for e in poll(client)] == ["DEVICE_SWAP"]
    r = client.post("/device-swap/v1/check", json={"phoneNumber": "+99970000001"}, headers=auth)
    assert r.json() == {"swapped": True}
    r = client.post("/device-swap/v1/retrieve-date", json={"phoneNumber": "+99970000001"}, headers=auth)
    assert r.json()["latestDeviceChange"]
    # SIM swap untouched
    assert client.post("/sim-swap/v2/check", json={"phoneNumber": "+99970000001"}, headers=auth).json() == {"swapped": False}


@pytest.mark.parametrize("path", ["/sim-swap/v2/check", "/device-swap/v1/retrieve-date"])
@pytest.mark.parametrize("body,status,code", [
    ({}, 422, "MISSING_IDENTIFIER"),
    ({"phoneNumber": "+15550000000"}, 404, "IDENTIFIER_NOT_FOUND"),
    ({"phoneNumber": "123"}, 400, "INVALID_ARGUMENT"),
])
def test_swap_errors(client, auth, path, body, status, code):
    r = client.post(path, json=body, headers=auth)
    assert (r.status_code, r.json()["code"]) == (status, code)


def test_max_age_out_of_range(client, auth):
    r = client.post("/sim-swap/v2/check", json={"phoneNumber": "+99970000001", "maxAge": 2401}, headers=auth)
    assert r.status_code == 400


def test_generic_scope_accepted(client):
    from camara_gw.auth.tokens import issue_token
    tok = issue_token(client.app.state.config, "demo-app", ["sim-swap"])["access_token"]
    r = client.post("/sim-swap/v2/check", json={"phoneNumber": "+99970000001"}, headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200


# ── SIM swap subscriptions ───────────────────────────────────────────────────
def sub_body(**over):
    b = {"protocol": "HTTP", "sink": "http://sink.example/events", "types": [SUB_TYPE],
         "config": {"subscriptionDetail": {"phoneNumber": "+99970000001"}}}
    b.update(over)
    return b


def test_subscription_lifecycle_and_swapped_event(client, auth, store, core):
    poll(client)
    r = client.post("/sim-swap-subscriptions/v0.4/subscriptions", json=sub_body(), headers=auth)
    assert r.status_code == 201, r.text
    sub = r.json()
    assert sub["status"] == "ACTIVE" and sub["config"]["subscriptionDetail"] == {"phoneNumber": "+99970000001"}

    lst = client.get("/sim-swap-subscriptions/v0.4/subscriptions", headers=auth)
    assert lst.json()["pagination"]["totalCount"] == 1 and lst.headers["X-Total-Count"] == "1"

    move_msisdn(store, "99970000001", "999700000000001", "999700000000002")
    import asyncio
    for ev in poll(client):
        client.portal.call(client.app.state.swaps.listeners[0], ev)
    ev = wait_for(lambda: [e for e in core.sink_events if e["type"] == SUB_TYPE])[0]
    assert ev["data"] == {"phoneNumber": "+99970000001", "subscriptionId": sub["id"]}

    assert client.delete(f"/sim-swap-subscriptions/v0.4/subscriptions/{sub['id']}", headers=auth).status_code == 204
    ended = wait_for(lambda: [e for e in core.sink_events if e["type"].endswith("subscription-ended")])[0]
    assert ended["data"]["terminationReason"] == "SUBSCRIPTION_DELETED"
    assert client.get(f"/sim-swap-subscriptions/v0.4/subscriptions/{sub['id']}", headers=auth).status_code == 404


def test_subscription_max_events(client, auth, store, core):
    poll(client)
    sid = client.post("/sim-swap-subscriptions/v0.4/subscriptions",
                      json=sub_body(config={"subscriptionDetail": {"phoneNumber": "+99970000001"}, "subscriptionMaxEvents": 1}),
                      headers=auth).json()["id"]
    client.portal.call(client.app.state.subscriptions.emit, "sim-swap-subscriptions", SUB_TYPE,
                       {"msisdn": "99970000001"}, {"phoneNumber": "+99970000001"})
    ended = wait_for(lambda: [e for e in core.sink_events if e["type"].endswith("subscription-ended")])[0]
    assert ended["data"] == {"subscriptionId": sid, "terminationReason": "MAX_EVENTS_REACHED"}


def test_subscription_expiry(client, auth, core):
    exp = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
    client.post("/sim-swap-subscriptions/v0.4/subscriptions",
                json=sub_body(config={"subscriptionDetail": {"phoneNumber": "+99970000001"}, "subscriptionExpireTime": exp}),
                headers=auth)
    ended = wait_for(lambda: [e for e in core.sink_events if e["type"].endswith("subscription-ended")], 4)[0]
    assert ended["data"]["terminationReason"] == "SUBSCRIPTION_EXPIRED"


@pytest.mark.parametrize("over,status,code", [
    ({"protocol": "MQTT3"}, 400, "INVALID_PROTOCOL"),
    ({"types": ["org.camaraproject.sim-swap-subscriptions.v0.other"]}, 403, "PERMISSION_DENIED"),
    ({"config": {"subscriptionDetail": {}}}, 422, "MISSING_IDENTIFIER"),
    ({"config": {"subscriptionDetail": {"phoneNumber": "+15550000000"}}}, 404, "IDENTIFIER_NOT_FOUND"),
    ({"config": {"subscriptionDetail": {"phoneNumber": "+99970000001"}, "subscriptionExpireTime": "2020-01-01T00:00:00Z"}}, 400, "OUT_OF_RANGE"),
    ({"sink": "ftp://x"}, 400, "INVALID_SINK"),
])
def test_subscription_errors(client, auth, over, status, code):
    r = client.post("/sim-swap-subscriptions/v0.4/subscriptions", json=sub_body(**over), headers=auth)
    assert (r.status_code, r.json()["code"]) == (status, code), r.text


def test_duplicate_subscription(client, auth):
    assert client.post("/sim-swap-subscriptions/v0.4/subscriptions", json=sub_body(), headers=auth).status_code == 201
    r = client.post("/sim-swap-subscriptions/v0.4/subscriptions", json=sub_body(), headers=auth)
    assert r.status_code == 409 and r.json()["code"] == "ALREADY_EXISTS"
