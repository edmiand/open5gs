#!/usr/bin/env bash
# Phase 4: Device Roaming Status + subscriptions.
#
#   ./demo/sink.py &        # watch roaming-on / roaming-change-country / roaming-off arrive
#   ./demo/04-roaming.sh    # PHONE, ROAM1 (default 20801 = FR), ROAM2 (26201 = DE)
#
# The lab is single-PLMN (UERANSIM can't do real roaming), so roaming is simulated with the
# LAB override (/_lab/roaming-override). The status itself is read from the AMF (/ue-info).
. "$(dirname "$0")/lib.sh"
PHONE="${PHONE:-+99970000001}"
SINK="${SINK:-http://127.0.0.1:9099/events}"
ROAM1="${ROAM1:-20801}"; ROAM2="${ROAM2:-26201}"
WAIT="${WAIT:-3}"
B=org.camaraproject.device-roaming-status-subscriptions.v0
TOKEN=$(token "device-roaming-status:read lab:write device-roaming-status-subscriptions:read device-roaming-status-subscriptions:delete device-roaming-status-subscriptions:$B.roaming-status:create device-roaming-status-subscriptions:$B.roaming-on:create device-roaming-status-subscriptions:$B.roaming-off:create device-roaming-status-subscriptions:$B.roaming-change-country:create")
DEV="{\"phoneNumber\":\"$PHONE\"}"
ovr() { call POST /_lab/roaming-override "{\"device\":$DEV,\"servingPlmn\":$1}" >/dev/null 2>&1; echo "   [lab] serving PLMN -> $1"; }

echo "== current roaming status (from AMF)"; call POST /device-roaming-status/v1/retrieve "{\"device\":$DEV}"

echo "== subscribe: roaming-on, roaming-off, roaming-change-country"
IDS=()
for ev in roaming-on roaming-off roaming-change-country; do
  id=$(curl -fsS -X POST "$CAMARA_URL/device-roaming-status-subscriptions/v0.9/subscriptions" -H "Authorization: Bearer $TOKEN" \
       -H 'content-type: application/json' \
       -d "{\"protocol\":\"HTTP\",\"sink\":\"$SINK\",\"types\":[\"$B.$ev\"],\"config\":{\"subscriptionDetail\":{\"device\":$DEV}}}" | jq -r .id)
  echo "   $ev -> $id"; IDS+=("$id")
done

echo "== travel: $ROAM1";  ovr "\"$ROAM1\""; sleep "$WAIT"
call POST /device-roaming-status/v1/retrieve "{\"device\":$DEV}"
echo "== travel: $ROAM2";  ovr "\"$ROAM2\""; sleep "$WAIT"
call POST /device-roaming-status/v1/retrieve "{\"device\":$DEV}"
echo "== back home";       ovr null; sleep "$WAIT"
call POST /device-roaming-status/v1/retrieve "{\"device\":$DEV}"

echo "== list + delete subscriptions"
curl -fsS "$CAMARA_URL/device-roaming-status-subscriptions/v0.9/subscriptions" -H "Authorization: Bearer $TOKEN" | jq -c '.pagination'
for id in "${IDS[@]}"; do call DELETE "/device-roaming-status-subscriptions/v0.9/subscriptions/$id" | tail -1; done
