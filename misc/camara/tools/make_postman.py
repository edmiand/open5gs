#!/usr/bin/env python3
"""Generate demo/open5gs-camara.postman_collection.json (Postman v2.1; Bruno can import it).

  python3 tools/make_postman.py

Collection variables: baseUrl, clientId, clientSecret (fill in from config.yaml), phone, appServer,
sink. Requests store ids (sessionId, subscriptionId, profileId, ...) for the follow-up calls.
"""
import json
import os
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "demo", "open5gs-camara.postman_collection.json")
DEV = {"phoneNumber": "{{phone}}"}
B_RS = "org.camaraproject.device-roaming-status-subscriptions.v0"
B_SS = "org.camaraproject.sim-swap-subscriptions.v0"


def req(name, method, path, body=None, save=None, desc="", auth="bearer", query=None, form=None, headers=None):
    url_raw = "{{baseUrl}}" + path + (("?" + "&".join(f"{k}={v}" for k, v in query)) if query else "")
    r = {"name": name, "request": {
        "method": method,
        "header": [{"key": "x-correlator", "value": "{{$guid}}"}] + (headers or []),
        "url": {"raw": url_raw, "host": ["{{baseUrl}}"], "path": [p for p in path.strip("/").split("/")],
                **({"query": [{"key": k, "value": v} for k, v in query]} if query else {})},
        "description": desc}}
    if auth == "basic":
        r["request"]["auth"] = {"type": "basic", "basic": [{"key": "username", "value": "{{clientId}}"},
                                                           {"key": "password", "value": "{{clientSecret}}"}]}
    elif auth == "none":
        r["request"]["auth"] = {"type": "noauth"}
    if body is not None:
        r["request"]["header"].append({"key": "Content-Type", "value": "application/json"})
        r["request"]["body"] = {"mode": "raw", "raw": json.dumps(body, indent=2)}
    if form is not None:
        r["request"]["body"] = {"mode": "urlencoded",
                                "urlencoded": [{"key": k, "value": v} for k, v in form]}
    if save:
        lines = ["const j = pm.response.json();"] + \
                [f"if (j && j{path_js} !== undefined) pm.collectionVariables.set('{var}', j{path_js});"
                 for var, path_js in save]
        r["event"] = [{"listen": "test", "script": {"type": "text/javascript", "exec": lines}}]
    return r


def token(scopes):
    return req("Get token (client_credentials)", "POST", "/oauth2/token", auth="basic",
               form=[("grant_type", "client_credentials"), ("scope", scopes)],
               save=[("accessToken", ".access_token")])


NV_AUTH = req("Authorize — RUN FROM THE DEVICE (curl --interface uesimtun0)", "GET", "/oauth2/authorize", auth="none",
              query=[("response_type", "code"), ("client_id", "{{clientId}}"),
                     ("redirect_uri", "https://demo-app.example/callback"),
                     ("scope", "openid number-verification:verify"), ("state", "pm")],
              desc="Must reach the gateway over the device's mobile data path (http://10.45.0.1:8090). From Postman "
                   "(off-network) it returns error=access_denied — the 'cloned agent' case. Copy `code` from the "
                   "device's redirect into the collection variable authCode (valid 60 s).")
B_DI = "device-identifier"

folders = [
    ("0 · Setup", [
        req("Health", "GET", "/health", auth="none"),
        req("OIDC discovery", "GET", "/.well-known/openid-configuration", auth="none"),
    ]),
    ("Story 1 · Identity — the cloned agent can't act", [
        NV_AUTH,
        req("1 · Exchange code → single-use token (sub = SIM)", "POST", "/oauth2/token", auth="basic",
            form=[("grant_type", "authorization_code"), ("code", "{{authCode}}"),
                  ("redirect_uri", "https://demo-app.example/callback")],
            save=[("accessToken", ".access_token")]),
        req("1 · Number Verification — is this the registered MSISDN?", "POST", "/number-verification/v2/verify",
            {"phoneNumber": "{{phone}}"}),
        token(f"{B_DI}:match-identifier {B_DI}:retrieve-identifier {B_DI}:retrieve-type {B_DI}:retrieve-ppid "
              "device-swap:check device-swap:retrieve-date"),
        req("2 · Device Identifier — match registered IMEI", "POST", "/device-identifier/v0.4/match-identifier",
            {"device": DEV, "imei": "{{registeredImei}}"}),
        req("2 · Device Identifier — retrieve identifier", "POST", "/device-identifier/v0.4/retrieve-identifier", {"device": DEV}),
        req("2 · Device Identifier — retrieve type", "POST", "/device-identifier/v0.4/retrieve-type", {"device": DEV}),
        req("2 · Device Identifier — retrieve PPID", "POST", "/device-identifier/v0.4/retrieve-ppid", {"device": DEV}),
        req("3 · Device Swap — check (24 h)", "POST", "/device-swap/v1/check", {"phoneNumber": "{{phone}}", "maxAge": 24}),
        req("3 · Device Swap — retrieve date", "POST", "/device-swap/v1/retrieve-date", {"phoneNumber": "{{phone}}"}),
    ]),
    ("Story 2 · Location — approved country?", [
        token(f"device-roaming-status:read lab:write device-roaming-status-subscriptions:read "
              f"device-roaming-status-subscriptions:delete "
              + " ".join(f"device-roaming-status-subscriptions:{B_RS}.{e}:create"
                         for e in ("roaming-status", "roaming-on", "roaming-off", "roaming-change-country"))),
        req("Roaming status now", "POST", "/device-roaming-status/v1/retrieve", {"device": DEV}),
        req("Subscribe: roaming-change-country", "POST", "/device-roaming-status-subscriptions/v0.9/subscriptions",
            {"protocol": "HTTP", "sink": "{{sink}}", "types": [f"{B_RS}.roaming-change-country"],
             "config": {"subscriptionDetail": {"device": DEV}}}, save=[("roamingSubscriptionId", ".id")]),
        req("Subscribe: roaming-on", "POST", "/device-roaming-status-subscriptions/v0.9/subscriptions",
            {"protocol": "HTTP", "sink": "{{sink}}", "types": [f"{B_RS}.roaming-on"],
             "config": {"subscriptionDetail": {"device": DEV}}}, save=[("roamingOnSubscriptionId", ".id")]),
        req("LAB: travel to approved country (DE 262/01)", "POST", "/_lab/roaming-override", {"device": DEV, "servingPlmn": "26201"}),
        req("Roaming status (approved)", "POST", "/device-roaming-status/v1/retrieve", {"device": DEV}),
        req("LAB: travel to non-approved country (US 310/260)", "POST", "/_lab/roaming-override", {"device": DEV, "servingPlmn": "310260"}),
        req("Roaming status (non-approved)", "POST", "/device-roaming-status/v1/retrieve", {"device": DEV}),
        req("LAB: back home", "POST", "/_lab/roaming-override", {"device": DEV, "servingPlmn": None}),
        req("List roaming subscriptions", "GET", "/device-roaming-status-subscriptions/v0.9/subscriptions"),
        req("Delete change-country subscription", "DELETE",
            "/device-roaming-status-subscriptions/v0.9/subscriptions/{{roamingSubscriptionId}}"),
        req("Delete roaming-on subscription", "DELETE",
            "/device-roaming-status-subscriptions/v0.9/subscriptions/{{roamingOnSubscriptionId}}"),
    ]),
    ("Story 3 · Quality — priority only when needed", [
        token("qos-profiles:read quality-on-demand:sessions:create quality-on-demand:sessions:read "
              "quality-on-demand:sessions:update quality-on-demand:sessions:delete "
              "quality-on-demand:sessions:retrieve-by-device application-profiles:create application-profiles:read "
              "application-profiles:delete connectivity-insights:check lab:read"),
        req("QoS profiles offered", "POST", "/qos-profiles/v1/retrieve-qos-profiles", {"status": "ACTIVE"}),
        req("(optional) Create live-video application profile", "POST", "/application-profiles/v0.6/application-profiles",
            {"networkQualityThresholds": {"packetDelayBudget": {"value": 100, "unit": "Milliseconds"},
                                          "jitter": {"value": 30, "unit": "Milliseconds"}, "packetLossErrorRate": 2}},
            save=[("profileId", ".applicationProfileId")]),
        req("(optional) Connectivity Insights — can the network meet it?", "POST", "/connectivity-insights/v0.7/check-network-quality",
            {"applicationProfileId": "{{profileId}}", "device": DEV, "applicationServer": {"ipv4Address": "{{appServer}}"}}),
        req("(optional) LAB: raw measurement", "GET", "/_lab/connectivity-insights/last"),
        req("Video tool opened → QoD session QOS_M, 30 min", "POST", "/quality-on-demand/v1/sessions",
            {"device": DEV, "applicationServer": {"ipv4Address": "{{appServer}}"}, "qosProfile": "QOS_M",
             "duration": 1800, "sink": "{{sink}}"}, save=[("sessionId", ".sessionId")],
            desc="⚠ With UERANSIM the gNB drops N2 when the QoS flow reaches the radio. Run this last."),
        req("Session status (expect AVAILABLE)", "GET", "/quality-on-demand/v1/sessions/{{sessionId}}"),
        req("Sessions of the device", "POST", "/quality-on-demand/v1/retrieve-sessions", {"device": DEV}),
        req("Video tool closed → release priority", "DELETE", "/quality-on-demand/v1/sessions/{{sessionId}}"),
        req("(optional) Delete application profile", "DELETE", "/application-profiles/v0.6/application-profiles/{{profileId}}"),
    ]),
    ("Extras · SIM Swap + lab helpers", [
        token(f"sim-swap:retrieve-date sim-swap:check sim-swap-subscriptions:{B_SS}.swapped:create "
              "sim-swap-subscriptions:read sim-swap-subscriptions:delete lab:read"),
        req("Resolve device (lab)", "POST", "/_lab/resolve", {"device": DEV}),
        req("SIM swap — check (24 h)", "POST", "/sim-swap/v2/check", {"phoneNumber": "{{phone}}", "maxAge": 24}),
        req("SIM swap — retrieve date", "POST", "/sim-swap/v2/retrieve-date", {"phoneNumber": "{{phone}}"}),
        req("Subscribe: SIM swapped", "POST", "/sim-swap-subscriptions/v0.4/subscriptions",
            {"protocol": "HTTP", "sink": "{{sink}}", "types": [f"{B_SS}.swapped"],
             "config": {"subscriptionDetail": {"phoneNumber": "{{phone}}"}}}, save=[("simSwapSubscriptionId", ".id")]),
        req("Delete SIM swap subscription", "DELETE", "/sim-swap-subscriptions/v0.4/subscriptions/{{simSwapSubscriptionId}}"),
    ]),
]

coll = {
    "info": {"_postman_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "open5gs-camara")), "name": "open5gs-camara (CAMARA lab)",
             "description": "CAMARA APIs served by the open5gs-camara gateway. Set clientSecret from misc/camara/config.yaml. "
                            "Each folder starts with a token request that stores {{accessToken}}.",
             "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
    "auth": {"type": "bearer", "bearer": [{"key": "token", "value": "{{accessToken}}", "type": "string"}]},
    "variable": [{"key": k, "value": v} for k, v in [
        ("baseUrl", "http://192.168.64.2:8090"), ("clientId", "demo-app"), ("clientSecret", "<from config.yaml>"),
        ("phone", "+99970000001"), ("registeredImei", "437081612581614"), ("appServer", "198.51.100.10"), ("sink", "http://192.168.64.2:9099/events"),
        ("accessToken", ""), ("authCode", ""), ("sessionId", ""), ("profileId", ""),
        ("simSwapSubscriptionId", ""), ("roamingSubscriptionId", ""), ("roamingOnSubscriptionId", "")]],
    "item": [{"name": n, "item": items} for n, items in folders],
}
with open(OUT, "w") as f:
    json.dump(coll, f, indent=2, ensure_ascii=False)
print(f"wrote {os.path.relpath(OUT)} ({sum(len(i) for _, i in folders)} requests)")
