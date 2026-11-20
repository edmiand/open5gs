#!/usr/bin/env bash
# Story 2 — Location: "is the agent used from an approved country?"
# Roaming status on demand + alerts when roaming starts/ends/changes country.
# UERANSIM can't roam: the visited network is simulated with the LAB override (logged as such).
#
#   ./demo/sink.py &                 # watch the alerts
#   ./demo/story2-location.sh        # APPROVED=310260 (US) NOT_APPROVED=26201 (DE) overridable; PAUSE=0 scripted
. "$(dirname "$0")/lib.sh"
PHONE="${PHONE:-+99970000001}"
SINK="${SINK:-http://127.0.0.1:9099/events}"
APPROVED="${APPROVED:-310260}"; NOT_APPROVED="${NOT_APPROVED:-26201}"; WAIT="${WAIT:-3}"
B=org.camaraproject.device-roaming-status-subscriptions.v0
S=device-roaming-status-subscriptions
TOKEN=$(token "device-roaming-status:read lab:write $S:read $S:delete $S:$B.roaming-on:create $S:$B.roaming-off:create $S:$B.roaming-change-country:create")
DEV="{\"phoneNumber\":\"$PHONE\"}"
status() { post_json /device-roaming-status/v1/retrieve "{\"device\":$DEV}"; fact "roaming=$(jq -r .roaming <<<"$LAST")  country=$(jq -c '.countryName // ["home network"]' <<<"$LAST")  lastStatusTime=$(jq -r .lastStatusTime <<<"$LAST")"; }
travel() { curl -fsS -X POST "$CAMARA_URL/_lab/roaming-override" -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
           -d "{\"device\":$DEV,\"servingPlmn\":$1}" >/dev/null; note "[LAB] serving PLMN → $1"; sleep "$WAIT"; }

step "1 · Where is the line right now? (read from the AMF)"; status
step "2 · Subscribe to roaming-on, roaming-off and roaming-change-country alerts (sink $SINK)"
IDS=()
for ev in roaming-on roaming-off roaming-change-country; do
  post_json /$S/v0.9/subscriptions "{\"protocol\":\"HTTP\",\"sink\":\"$SINK\",\"types\":[\"$B.$ev\"],\"config\":{\"subscriptionDetail\":{\"device\":$DEV}}}" >/dev/null
  IDS+=("$(jq -r .id <<<"$LAST")"); fact "$ev → ${IDS[-1]}"
done
step "3 · The unit travels to an approved country ($APPROVED)"; travel "\"$APPROVED\""; status
step "4 · …then to a non-approved country ($NOT_APPROVED)"; travel "\"$NOT_APPROVED\""; status
step "5 · Back on the home network"; travel null; status
step "6 · Clean up subscriptions"
for id in "${IDS[@]}"; do call DELETE "/$S/v0.9/subscriptions/$id" | tail -1; done
note "(the sink received roaming-on, roaming-change-country, roaming-off and subscription lifecycle events)"
