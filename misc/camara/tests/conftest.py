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
            {"client_id": "demo-app", "client_secret": "s3cret", "scopes": ["lab:read"]},
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
            return httpx.Response(404, json={"status": 404})
        return httpx.Response(404, content=json.dumps({"status": 404}))


@pytest.fixture
def core():
    return FakeCore()


@pytest.fixture
def store():
    db = mongomock.MongoClient()["open5gs"]
    db.subscribers.insert_many([dict(UE1), dict(UE2)])
    return Store(db)


@pytest.fixture
def client(core, store):
    cfg = make_cfg()
    t = httpx.MockTransport(core.handler)
    app = create_app(cfg, store=store, sbi=SbiClient(cfg.sbi, transport=t), info=InfoClient(cfg.info, transport=t))
    with TestClient(app) as c:
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
