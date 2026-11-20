"""Shared fixtures: config, mongomock store, MockTransport-backed core clients."""

from __future__ import annotations

import json

import httpx
import mongomock
import pytest
from fastapi.testclient import TestClient

from camara_gw.app import create_app
from camara_gw.config import Config
from camara_gw.core.info import InfoClient
from camara_gw.core.mongo import Store
from camara_gw.core.sbi import SbiClient
from camara_gw.notify import Notifier

UE1 = {"imsi": "999700000000001", "msisdn": ["99970000001"], "imeisv": "4370816125816151"}
UE2 = {"imsi": "999700000000002", "msisdn": ["99970000002"], "imeisv": "4370816125816152"}

PDU_INFO = {
    "items": [
        {"supi": "imsi-999700000000001", "ue_activity": "active",
         "pdu": [{"psi": 1, "dnn": "internet", "ipv4": "10.45.0.2", "ipv6": "2001:db8:cafe::2",
                  "snssai": {"sst": 1, "sd": "ffffff"}, "qos_flows": [{"qfi": 1, "5qi": 9}],
                  "pdu_state": "active"}]},
        {"supi": "imsi-999700000000002", "ue_activity": "active",
         "pdu": [{"psi": 1, "dnn": "internet", "ipv4": "10.45.0.3",
                  "snssai": {"sst": 1}, "qos_flows": [{"qfi": 1, "5qi": 9}], "pdu_state": "active"}]},
    ],
    "pager": {"page": 0, "page_size": 100, "count": 2},
}

UE_INFO = {"items": [{"supi": "imsi-999700000000001", "pei": "imeisv-4370816125816151",
                      "cm_state": "connected",
                      "location": {"nr_tai": {"plmn": "99970", "tac": 1}}}],
           "pager": {"page": 0, "page_size": 100, "count": 1}}


def make_cfg(**over) -> Config:
    base = {
        "auth": {"signing_key": "test-key-0123456789abcdef0123456789", "clients": [
            {"client_id": "demo-app", "client_secret": "s3cret", "scopes": ["lab:read", "qos-profiles:read", "quality-on-demand:sessions:create", "quality-on-demand:sessions:read", "quality-on-demand:sessions:update", "quality-on-demand:sessions:delete", "quality-on-demand:sessions:retrieve-by-device", "sim-swap:retrieve-date", "sim-swap:check", "device-swap:retrieve-date", "device-swap:check", "sim-swap-subscriptions:org.camaraproject.sim-swap-subscriptions.v0.swapped:create", "sim-swap-subscriptions:read", "sim-swap-subscriptions:delete", "openid", "number-verification:verify", "number-verification:device-phone-number:read", "lab:write", "device-roaming-status:read", "device-roaming-status-subscriptions:org.camaraproject.device-roaming-status-subscriptions.v0.roaming-status:create", "device-roaming-status-subscriptions:org.camaraproject.device-roaming-status-subscriptions.v0.roaming-on:create", "device-roaming-status-subscriptions:org.camaraproject.device-roaming-status-subscriptions.v0.roaming-off:create", "device-roaming-status-subscriptions:org.camaraproject.device-roaming-status-subscriptions.v0.roaming-change-country:create", "device-roaming-status-subscriptions:read", "device-roaming-status-subscriptions:delete"], "redirect_uris": ["https://demo-app.example/callback"]},
            {"client_id": "no-scope", "client_secret": "s3cret", "scopes": []},
        ]},
        "lab_endpoints": True,
    }
    base.update(over)
    return Config.model_validate(base)


class FakeCore:
    """Routes requests for both the SBI and info clients; tests can tweak behaviour."""

    def __init__(self):
        self.pdu_info = PDU_INFO
        self.ue_info = UE_INFO
        self.nrf_down: set[str] = set()
        self.calls: list[httpx.Request] = []
        self.pcf_status = 201
        self.pcf_bodies: list[dict] = []
        self.pcf_deletes: list[str] = []
        self.next_app_id = 1
        self.sink_events: list[dict] = []

    def add_flow(self, supi: str, qfi: int, fiveqi: int):
        import copy
        self.pdu_info = copy.deepcopy(self.pdu_info)
        for it in self.pdu_info["items"]:
            if it["supi"] == supi:
                it["pdu"][0]["qos_flows"].append({"qfi": qfi, "5qi": fiveqi})

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path
        if path == "/pdu-info":
            return httpx.Response(200, json=self.pdu_info)
        if path == "/ue-info":
            return httpx.Response(200, json=self.ue_info)
        if path == "/nnrf-nfm/v1/nf-instances":
            nf = request.url.params["nf-type"]
            items = [] if nf in self.nrf_down else [{"href": f"http://127.0.0.10:7777/nnrf-nfm/v1/nf-instances/{nf}-1"}]
            return httpx.Response(200, json={"_links": {"item": items}})
        if path.startswith("/nnrf-nfm/v1/nf-instances/"):
            nf = path.rsplit("/", 1)[1].split("-")[0]
            return httpx.Response(200, json={"nfInstanceId": path, "nfType": nf, "nfStatus": "REGISTERED"})
        if path == "/nbsf-management/v1/pcfBindings":
            ip = request.url.params.get("ipv4Addr")
            known = {pdu.get("ipv4") for it in self.pdu_info["items"] for pdu in it["pdu"]}
            if ip in known:
                return httpx.Response(200, json={"dnn": "internet",
                                                 "pcfIpEndPoints": [{"ipv4Address": "127.0.0.13", "port": 7777}]})
            return httpx.Response(404, json={"status": 404})
        if path == "/npcf-policyauthorization/v1/app-sessions" and request.method == "POST":
            self.pcf_bodies.append(json.loads(request.content))
            if self.pcf_status != 201:
                return httpx.Response(self.pcf_status, json={"status": self.pcf_status, "detail": "pcf says no"})
            aid = self.next_app_id
            self.next_app_id += 1
            return httpx.Response(201, json={}, headers={
                "location": f"http://127.0.0.13:7777/npcf-policyauthorization/v1/app-sessions/{aid}"})
        if path.startswith("/npcf-policyauthorization/v1/app-sessions/") and path.endswith("/delete"):
            self.pcf_deletes.append(path)
            return httpx.Response(204)
        if request.url.host == "sink.example":
            self.sink_events.append(json.loads(request.content))
            return httpx.Response(204)
        return httpx.Response(404, content=json.dumps({"status": 404}))


@pytest.fixture
def core():
    return FakeCore()


@pytest.fixture
def store():
    db = mongomock.MongoClient(tz_aware=True)["open5gs"]
    db.subscribers.insert_many([dict(UE1), dict(UE2)])
    return Store(db)


def build_client(core, store, client_addr=("127.0.0.1", 50000), **cfg_over):
    cfg = make_cfg(**cfg_over)
    t = httpx.MockTransport(core.handler)
    app = create_app(cfg, store=store, sbi=SbiClient(cfg.sbi, transport=t), info=InfoClient(cfg.info, transport=t),
                     notifier=Notifier(cfg.notify, source="test", transport=t))
    return TestClient(app, client=client_addr, follow_redirects=False)


@pytest.fixture
def client(core, store):
    with build_client(core, store, notify={"allow_http_sink": True},
                      qod={"verify_timeout": 2.0, "expiry_interval": 0.2},
                      swap={"enabled": False}, roaming={"enabled": False}) as c:
        yield c


@pytest.fixture
def token(client):
    r = client.post("/oauth2/token", data={"grant_type": "client_credentials",
                                           "client_id": "demo-app", "client_secret": "s3cret"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture
def auth(token):
    return {"Authorization": f"Bearer {token}"}
