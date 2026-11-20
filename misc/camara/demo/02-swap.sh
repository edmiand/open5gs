#!/usr/bin/env bash
# Phase 2: SIM Swap, Device Swap, SIM Swap Subscriptions.
#
#   ./demo/sink.py &      # watch the 'swapped' CloudEvent arrive
#   ./demo/02-swap.sh     # PHONE, SPARE_IMSI, NEW_IMEISV overridable
#
# Uses tools/lab_swap.py to perform the provisioning changes (WebUI equivalent), then
# reverses them at the end (RESTORE=0 to keep the swapped state).
. "$(dirname "$0")/lib.sh"
PHONE="${PHONE:-+99970000001}"
MSISDN="${PHONE#+}"
SINK="${SINK:-http://127.0.0.1:9099/events}"
NEW_IMEISV="${NEW_IMEISV:-3528820000000101}"
POLL="${POLL:-3}"                  # > gateway swap.poll_interval
LAB="$(dirname "$0")/../.venv/bin/python $(dirname "$0")/../tools/lab_swap.py"

TOKEN=$(token "sim-swap:retrieve-date sim-swap:check device-swap:retrieve-date device-swap:check sim-swap-subscriptions:org.camaraproject.sim-swap-subscriptions.v0.swapped:create sim-swap-subscriptions:read sim-swap-subscriptions:delete")

ORIG_IMSI=$($LAB show | awk -v m="'$MSISDN'" 'index($0,m){print $1; exit}')
[[ -n $ORIG_IMSI ]] || { echo "MSISDN $MSISDN not assigned to any IMSI"; exit 1; }
SPARE_IMSI="${SPARE_IMSI:-${ORIG_IMSI%?}$(( (${ORIG_IMSI: -1} + 1) % 10 ))}"
ORIG_IMEISV=$($LAB show | awk -v i="$ORIG_IMSI" '$1==i{sub("imeisv=","",$3); print $3}')
echo "phone $PHONE on IMSI $ORIG_IMSI (imeisv $ORIG_IMEISV); spare SIM $SPARE_IMSI"
$LAB show | grep -q "^$SPARE_IMSI " || $LAB add-spare --from "$ORIG_IMSI" --imsi "$SPARE_IMSI"

echo "== before"
call POST /sim-swap/v2/retrieve-date "{\"phoneNumber\":\"$PHONE\"}"
call POST /sim-swap/v2/check "{\"phoneNumber\":\"$PHONE\",\"maxAge\":24}"
call POST /device-swap/v1/check "{\"phoneNumber\":\"$PHONE\",\"maxAge\":24}"

echo "== subscribe to SIM swap events"
sub=$(curl -fsS -X POST "$CAMARA_URL/sim-swap-subscriptions/v0.4/subscriptions" -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d "{\"protocol\":\"HTTP\",\"sink\":\"$SINK\",\"types\":[\"org.camaraproject.sim-swap-subscriptions.v0.swapped\"],\"config\":{\"subscriptionDetail\":{\"phoneNumber\":\"$PHONE\"}}}")
echo "$sub" | jq .; SUB_ID=$(echo "$sub" | jq -r .id)

echo "== SIM SWAP: move $MSISDN to $SPARE_IMSI"; $LAB move-msisdn "$MSISDN" --to "$SPARE_IMSI"; sleep "$POLL"
call POST /sim-swap/v2/retrieve-date "{\"phoneNumber\":\"$PHONE\"}"
call POST /sim-swap/v2/check "{\"phoneNumber\":\"$PHONE\",\"maxAge\":24}"

echo "== move back to $ORIG_IMSI (second swap)"; $LAB move-msisdn "$MSISDN" --to "$ORIG_IMSI"; sleep "$POLL"

echo "== DEVICE SWAP: imeisv of $ORIG_IMSI -> $NEW_IMEISV"; $LAB set-imeisv "$ORIG_IMSI" "$NEW_IMEISV"; sleep "$POLL"
call POST /device-swap/v1/retrieve-date "{\"phoneNumber\":\"$PHONE\"}"
call POST /device-swap/v1/check "{\"phoneNumber\":\"$PHONE\",\"maxAge\":24}"

echo "== delete subscription"; call DELETE "/sim-swap-subscriptions/v0.4/subscriptions/$SUB_ID"

if [[ ${RESTORE:-1} == 1 && $ORIG_IMEISV =~ ^[0-9]{16}$ ]]; then
  echo "== restore imeisv $ORIG_IMEISV (the next UE registration rewrites it anyway)"
  $LAB set-imeisv "$ORIG_IMSI" "$ORIG_IMEISV"
fi
