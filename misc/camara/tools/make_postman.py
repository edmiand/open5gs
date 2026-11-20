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


folders = [
    ("0 · Foundations", [
        req("Health", "GET", "/health", auth="none"),
        req("OIDC discovery", "GET", "/.well-known/openid-configuration", auth="none"),
        token("lab:read lab:write"),
        req("Resolve device (lab)", "POST", "/_lab/resolve", {"device": DEV}),
    ]),
    ("1 · QoS Profiles + Quality on Demand", [
        token("qos-profiles:read quality-on-demand:sessions:create quality-on-demand:sessions:read "
              "quality-on-demand:sessions:update quality-on-demand:sessions:delete "
              "quality-on-demand:sessions:retrieve-by-device"),
        req("Retrieve QoS profiles", "POST", "/qos-profiles/v1/retrieve-qos-profiles", {"status": "ACTIVE"}),
        req("Get QoS profile QOS_E", "GET", "/qos-profiles/v1/qos-profiles/QOS_E"),
        req("Create QoD session", "POST", "/quality-on-demand/v1/sessions",
            {"device": DEV, "applicationServer": {"ipv4Address": "{{appServer}}"}, "qosProfile": "QOS_E",
             "duration": 120, "sink": "{{sink}}"}, save=[("sessionId", ".sessionId")]),
        req("Get QoD session", "GET", "/quality-on-demand/v1/sessions/{{sessionId}}"),
        req("Extend QoD session", "POST", "/quality-on-demand/v1/sessions/{{sessionId}}/extend",
            {"requestedAdditionalDuration": 60}),
        req("Retrieve sessions by device", "POST", "/quality-on-demand/v1/retrieve-sessions", {"device": DEV}),
        req("Delete QoD session", "DELETE", "/quality-on-demand/v1/sessions/{{sessionId}}"),
    ]),
    ("2 · SIM Swap + Device Swap + subscriptions", [
        token(f"sim-swap:retrieve-date sim-swap:check device-swap:retrieve-date device-swap:check "
              f"sim-swap-subscriptions:{B_SS}.swapped:create sim-swap-subscriptions:read sim-swap-subscriptions:delete"),
        req("SIM swap — retrieve date", "POST", "/sim-swap/v2/retrieve-date", {"phoneNumber": "{{phone}}"}),
        req("SIM swap — check (24 h)", "POST", "/sim-swap/v2/check", {"phoneNumber": "{{phone}}", "maxAge": 24}),
        req("Device swap — retrieve date", "POST", "/device-swap/v1/retrieve-date", {"phoneNumber": "{{phone}}"}),
        req("Device swap — check (24 h)", "POST", "/device-swap/v1/check", {"phoneNumber": "{{phone}}", "maxAge": 24}),
        req("Subscribe: SIM swapped", "POST", "/sim-swap-subscriptions/v0.4/subscriptions",
            {"protocol": "HTTP", "sink": "{{sink}}", "types": [f"{B_SS}.swapped"],
             "config": {"subscriptionDetail": {"phoneNumber": "{{phone}}"}}},
            save=[("simSwapSubscriptionId", ".id")],
            desc="Then move the MSISDN: tools/lab_swap.py move-msisdn <msisdn> --to <imsi>"),
        req("List SIM swap subscriptions", "GET", "/sim-swap-subscriptions/v0.4/subscriptions"),
        req("Delete SIM swap subscription", "DELETE", "/sim-swap-subscriptions/v0.4/subscriptions/{{simSwapSubscriptionId}}"),
    ]),
    ("3 · Number Verification (3-legged)", [
        req("Authorize — RUN FROM THE UE (curl --interface uesimtun0)", "GET", "/oauth2/authorize", auth="none",
            query=[("response_type", "code"), ("client_id", "{{clientId}}"),
                   ("redirect_uri", "https://demo-app.example/callback"),
                   ("scope", "openid number-verification:verify"), ("state", "pm")],
            desc="Must reach the gateway over the UE data path (http://10.45.0.1:8090). From Postman (off-network) this "
                 "returns error=access_denied — the expected negative case. Copy `code` from the device's redirect into "
                 "the collection variable authCode."),
        req("Exchange code → single-use token", "POST", "/oauth2/token", auth="basic",
            form=[("grant_type", "authorization_code"), ("code", "{{authCode}}"),
                  ("redirect_uri", "https://demo-app.example/callback")],
            save=[("accessToken", ".access_token")]),
        req("Verify phone number", "POST", "/number-verification/v2/verify", {"phoneNumber": "{{phone}}"}),
    ]),
    ("4 · Device Roaming Status + subscriptions", [
        token(f"device-roaming-status:read lab:write device-roaming-status-subscriptions:read "
              f"device-roaming-status-subscriptions:delete "
              + " ".join(f"device-roaming-status-subscriptions:{B_RS}.{e}:create"
                         for e in ("roaming-status", "roaming-on", "roaming-off", "roaming-change-country"))),
        req("Retrieve roaming status", "POST", "/device-roaming-status/v1/retrieve", {"device": DEV}),
        req("Subscribe: roaming-on", "POST", "/device-roaming-status-subscriptions/v0.9/subscriptions",
            {"protocol": "HTTP", "sink": "{{sink}}", "types": [f"{B_RS}.roaming-on"],
             "config": {"subscriptionDetail": {"device": DEV}}}, save=[("roamingSubscriptionId", ".id")]),
        req("LAB: travel to France (208/01)", "POST", "/_lab/roaming-override", {"device": DEV, "servingPlmn": "20801"}),
        req("LAB: back home", "POST", "/_lab/roaming-override", {"device": DEV, "servingPlmn": None}),
        req("List roaming subscriptions", "GET", "/device-roaming-status-subscriptions/v0.9/subscriptions"),
        req("Delete roaming subscription", "DELETE",
            "/device-roaming-status-subscriptions/v0.9/subscriptions/{{roamingSubscriptionId}}"),
    ]),
    ("5 · Application Profiles + Connectivity Insights", [
        token("application-profiles:create application-profiles:read application-profiles:update "
              "application-profiles:delete connectivity-insights:check lab:read"),
        req("Create application profile (cloud gaming)", "POST", "/application-profiles/v0.6/application-profiles",
            {"networkQualityThresholds": {"packetDelayBudget": {"value": 10, "unit": "Milliseconds"},
                                          "jitter": {"value": 5, "unit": "Milliseconds"}, "packetLossErrorRate": 2}},
            save=[("profileId", ".applicationProfileId")]),
        req("Get application profile", "GET", "/application-profiles/v0.6/application-profiles/{{profileId}}"),
        req("Check network quality", "POST", "/connectivity-insights/v0.7/check-network-quality",
            {"applicationProfileId": "{{profileId}}", "device": DEV,
             "applicationServer": {"ipv4Address": "{{appServer}}"}}),
        req("LAB: last measurement", "GET", "/_lab/connectivity-insights/last"),
        req("Delete application profile", "DELETE", "/application-profiles/v0.6/application-profiles/{{profileId}}"),
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
        ("phone", "+99970000001"), ("appServer", "198.51.100.10"), ("sink", "http://192.168.64.2:9099/events"),
        ("accessToken", ""), ("authCode", ""), ("sessionId", ""), ("profileId", ""),
        ("simSwapSubscriptionId", ""), ("roamingSubscriptionId", "")]],
    "item": [{"name": n, "item": items} for n, items in folders],
}
with open(OUT, "w") as f:
    json.dump(coll, f, indent=2, ensure_ascii=False)
print(f"wrote {os.path.relpath(OUT)} ({sum(len(i) for _, i in folders)} requests)")
