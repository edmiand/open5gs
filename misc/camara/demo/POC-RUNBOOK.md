# CAMARA POC — presenter runbook

Three user stories, run by hand against the lab carrier endpoint (Open5GS + `open5gs-camara`).
No EQTY component is involved: each story shows the **network facts** an AI-agent gateway would use to
allow or block an action. Background and design: [`../PLAN.md`](../PLAN.md) §0; architecture page: see README.

| Story | Question | APIs |
|---|---|---|
| 1 Identity | Which SIM and which device is this request really from? | Number Verification 2.1.1 · Device Identifier 0.4.0 · Device Swap 1.1.0 |
| 2 Location | Is the line roaming, and in which country? | Device Roaming Status 1.2.0 · Subscriptions 0.9.0 |
| 3 Quality | Priority for the live-video link, for a limited time | QoS Profiles 1.2.0 · Quality on Demand 1.2.0 · Connectivity Insights 0.7.0 (optional) |

---

## 1. Before the meeting (≈ 10 min)

On the **lab VM** (`~/open5gs/misc/camara`):

```bash
sudo systemctl restart open5gs-camara         # loads the current gateway (v0.8.0)
./demo/preflight.sh                           # must end with "0 to fix"
```

On the **UERANSIM host** (192.168.64.3): if pre-flight says the UE is idle or doesn't answer ping, restart the UE
(`nr-ue -c <ue config>`), then run `./demo/preflight.sh` again.

Leftover swap events from rehearsals make Story 1 show `swapped: true`. Clear them with
`.venv/bin/python tools/lab_swap.py reset-history`. This touches gateway state only, never `subscribers`.

**Terminal layout**

| Terminal | Where | Command |
|---|---|---|
| A — events | VM | `python3 demo/sink.py` (alerts appear here) |
| B — gateway log | VM | `journalctl -u open5gs-camara -f \| grep camara\.` |
| C — presenter | VM | the commands below |
| D — device | UERANSIM host | for Story 1 step 1b only |

## 2. Setup in terminal C

```bash
cd ~/open5gs/misc/camara
export GW=http://localhost:8090 PHONE=+99970000001 IMEI=437081612581614
export SECRET=$(.venv/bin/python -c "import yaml;print(next(c['client_secret'] for c in yaml.safe_load(open('config.yaml'))['auth']['clients'] if c['client_id']=='demo-app'))")
tok() { curl -s -u demo-app:$SECRET -d grant_type=client_credentials --data-urlencode "scope=$1" $GW/oauth2/token | jq -r .access_token; }
api() { curl -s -X POST "$GW$1" -H "Authorization: Bearer $T" -H 'content-type: application/json' -d "$2" | jq .; }
curl -s $GW/health | jq '{status, version}'
```

**One command for all three stories:** `./demo/stories.sh` runs pre-flight, then Stories 1 → 2 → 3, pausing between
steps and asking before Story 3. `ONLY="1 2"` runs a subset; `UE_SSH=…` automates the Story 1 device step.
Each story also runs on its own: `./demo/story1-identity.sh`, `./demo/story2-location.sh`, `./demo/story3-quality.sh`
(`PAUSE=0` = no pauses).

---

## 3. Story 1 — Identity: "the cloned agent can't act"

*A field unit is registered with MSISDN `+99970000001` and IMEI `437081612581614`. Before a high-risk action, ask
the network which SIM and which device the request really comes from.*

**1a · The cloned agent (off the mobile network) asks for a network identity.** No code is issued.
```bash
AUTHZ="/oauth2/authorize?response_type=code&client_id=demo-app&redirect_uri=https%3A%2F%2Fdemo-app.example%2Fcallback&scope=openid%20number-verification%3Averify&state=demo"
curl -s -o /dev/null -w '%{redirect_url}\n' "$GW$AUTHZ"
# → https://demo-app.example/callback?error=access_denied&...
```

**1b · The real unit** follows the same redirect over mobile data (terminal D, UERANSIM host):
```bash
curl -s -o /dev/null -w '%{redirect_url}\n' --interface uesimtun0 'http://10.45.0.1:8090/oauth2/authorize?response_type=code&client_id=demo-app&redirect_uri=https%3A%2F%2Fdemo-app.example%2Fcallback&scope=openid%20number-verification%3Averify&state=demo'
# → https://demo-app.example/callback?code=XXXX&state=demo        (code valid 60 s, single use)
```

**1c · The backend exchanges the code** for a single-use token whose subject is that SIM (terminal C):
```bash
CODE=XXXX     # paste from 1b
T=$(curl -s -u demo-app:$SECRET $GW/oauth2/token -d grant_type=authorization_code --data-urlencode "code=$CODE" \
      --data-urlencode redirect_uri=https://demo-app.example/callback | jq -r .access_token)
cut -d. -f2 <<<"$T" | tr '_-' '/+' | base64 -d 2>/dev/null | jq '{sub, amr, scope, exp}'
```

**1d · Number Verification:** is the request's SIM the registered number?
```bash
api /number-verification/v2/verify "{\"phoneNumber\":\"$PHONE\"}"      # → {"devicePhoneNumberVerified": true}
api /number-verification/v2/verify "{\"phoneNumber\":\"$PHONE\"}"      # same token again → 401 (single use)
```

**2 · Device Identifier:** is the device that SIM sits in right now the registered IMEI?
```bash
T=$(tok "device-identifier:match-identifier device-identifier:retrieve-identifier device-swap:check")
api /device-identifier/v0.4/match-identifier "{\"device\":{\"phoneNumber\":\"$PHONE\"},\"imei\":\"$IMEI\"}"           # match: true
api /device-identifier/v0.4/match-identifier "{\"device\":{\"phoneNumber\":\"$PHONE\"},\"imei\":\"490154203237518\"}"  # cloned unit: false
api /device-identifier/v0.4/retrieve-identifier "{\"device\":{\"phoneNumber\":\"$PHONE\"}}"                           # what the network sees
```

**3 · Device Swap:** did the device for this number change recently?
```bash
api /device-swap/v1/check "{\"phoneNumber\":\"$PHONE\",\"maxAge\":24}"     # → {"swapped": false}
```

*Talking points:*
- Wi-Fi, VPN or another carrier never gets a code (1a).
- The SIM is proven by the live data session, not by an OTP.
- `match-identifier` lets the gateway check the IMEI without learning it.
- The allow/block decision belongs to the EQTY gateway; the network supplies facts.

## 4. Story 2 — Location: "used from an approved country?"

*The line may only reach customer data from approved countries. UERANSIM can't roam, so travel is simulated with a
**lab override**. The status itself is read from the AMF.*

```bash
S=device-roaming-status-subscriptions; B=org.camaraproject.$S.v0
T=$(tok "device-roaming-status:read lab:write $S:read $S:delete $S:$B.roaming-on:create $S:$B.roaming-off:create $S:$B.roaming-change-country:create")
api /device-roaming-status/v1/retrieve "{\"device\":{\"phoneNumber\":\"$PHONE\"}}"            # roaming: false (home)
for e in roaming-on roaming-off roaming-change-country; do
  api /$S/v0.9/subscriptions "{\"protocol\":\"HTTP\",\"sink\":\"http://127.0.0.1:9099/events\",\"types\":[\"$B.$e\"],\"config\":{\"subscriptionDetail\":{\"device\":{\"phoneNumber\":\"$PHONE\"}}}}" | jq -r .id
done
api /_lab/roaming-override "{\"device\":{\"phoneNumber\":\"$PHONE\"},\"servingPlmn\":\"26201\"}"   # → Germany (approved)
api /device-roaming-status/v1/retrieve "{\"device\":{\"phoneNumber\":\"$PHONE\"}}"            # roaming: true, countryCode 262, ["DE"]
api /_lab/roaming-override "{\"device\":{\"phoneNumber\":\"$PHONE\"},\"servingPlmn\":\"310260\"}"  # → USA (not approved)
api /device-roaming-status/v1/retrieve "{\"device\":{\"phoneNumber\":\"$PHONE\"}}"            # ["US"]
api /_lab/roaming-override "{\"device\":{\"phoneNumber\":\"$PHONE\"},\"servingPlmn\":null}"       # home again
```
Terminal A shows `roaming-on` → `roaming-change-country` (US) → `roaming-off`.
Clean up: `curl -s $GW/$S/v0.9/subscriptions -H "Authorization: Bearer $T" | jq -r '.subscriptions[].id' | while read i; do curl -s -X DELETE $GW/$S/v0.9/subscriptions/$i -H "Authorization: Bearer $T"; done`

*Talking points:*
- The country comes from the network the device is attached to, not from GPS or the app.
- `lastStatusTime` tells the gateway how fresh the answer is: stale or unknown means block.

## 5. Story 3 — Quality: "priority only when the tool needs it" (run last)

> ⚠ With UERANSIM, the gNB drops its N2 link when the new QoS flow reaches the radio. The flow is real in the core
> (PCF/SMF), but the UE falls off afterwards. Run this story last, then restart nr-gnb and nr-ue.

```bash
T=$(tok "qos-profiles:read quality-on-demand:sessions:create quality-on-demand:sessions:read quality-on-demand:sessions:delete")
curl -s http://127.0.0.4:9090/pdu-info | jq -c '.items[] | .pdu[].qos_flows'          # text tools: default flow only (5QI 9)
api /qos-profiles/v1/retrieve-qos-profiles '{"status":"ACTIVE"}' | jq -c '.[] | {name, maxDownstreamRate, packetDelayBudget}'
SID=$(api /quality-on-demand/v1/sessions "{\"device\":{\"phoneNumber\":\"$PHONE\"},\"applicationServer\":{\"ipv4Address\":\"198.51.100.10\"},\"qosProfile\":\"QOS_M\",\"duration\":1800,\"sink\":\"http://127.0.0.1:9099/events\"}" | tee /dev/stderr | jq -r .sessionId)
sleep 2; curl -s $GW/quality-on-demand/v1/sessions/$SID -H "Authorization: Bearer $T" | jq '{qosStatus, startedAt, expiresAt}'   # AVAILABLE
curl -s http://127.0.0.4:9090/pdu-info | jq -c '.items[] | .pdu[].qos_flows'          # + new QoS flow (5QI 4)
curl -s -X DELETE $GW/quality-on-demand/v1/sessions/$SID -H "Authorization: Bearer $T" -w '%{http_code}\n'   # 204: priority released
```
Terminal A shows `qos-status-changed` AVAILABLE, then UNAVAILABLE (`DELETE_REQUESTED`).

*Optional: Connectivity Insights* (can the network meet a live-video profile now?) is part of
`./demo/story3-quality.sh`, step 2.

*Talking points:*
- Text tools never request priority.
- The QoS profile name maps to a 5QI in the PCF.
- "Active" is reported only after the SMF actually installs the flow.

---

## 6. If something goes wrong

| Symptom | Cause | Fix |
|---|---|---|
| Story 1b returns `error=access_denied` on the device | curl didn't use the mobile interface, or the UE has no PDU session | Use `--interface uesimtun0`; `./demo/preflight.sh`; restart nr-ue |
| `invalid_grant` at 1c | code older than 60 s or already used | Repeat 1b |
| `401 UNAUTHENTICATED` on verify | NV token is single-use | Repeat 1b–1c |
| Story 1 step 3 shows `swapped: true` | rehearsal residue | `.venv/bin/python tools/lab_swap.py reset-history` |
| Connectivity Insights "unable to meet" for everything | UE idle / unreachable after a gNB restart | Restart nr-ue; pre-flight "UE answers ping" must pass |
| QoD `SERVICE_NOT_APPLICABLE` "PCF has no policy session" | PCF restarted after the UE attached | Re-attach the UE |
| UE gone after Story 3 | UERANSIM limitation (see Story 3) | Restart nr-gnb and nr-ue |
| health `DOWN` | an NF isn't registered | `./open5gs-ctl.sh status`; restart the listed NF |

## 7. After the demo

```bash
.venv/bin/python tools/lab_swap.py reset-history     # optional: forget swaps made during the demo
```
Roaming overrides are cleared by Story 2 step "home again". If interrupted, run
`api /_lab/roaming-override "{\"device\":{\"phoneNumber\":\"$PHONE\"},\"servingPlmn\":null}"`.
