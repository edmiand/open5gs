#!/usr/bin/env bash
# Pre-flight checks before presenting the POC. Read-only; prints what to fix.
#   ./demo/preflight.sh            (PHONE, CAMARA_URL overridable)
. "$(dirname "$0")/lib.sh"
PHONE="${PHONE:-+99970000001}"
ok=0; bad=0
pass() { printf '  \033[32m✔\033[0m %s\n' "$*"; ok=$((ok+1)); }
fail() { printf '  \033[31m✘\033[0m %s\n      → %s\n' "$1" "$2"; bad=$((bad+1)); }

echo "Pre-flight for $CAMARA_URL"
h=$(curl -fsS -m5 "$CAMARA_URL/health" 2>/dev/null)
if [[ $(jq -r .status <<<"$h" 2>/dev/null) == UP ]]; then pass "gateway UP, version $(jq -r .version <<<"$h")"
else fail "gateway not UP" "sudo systemctl restart open5gs-camara; journalctl -u open5gs-camara -n 50"; fi
[[ $(jq -r .version <<<"$h" 2>/dev/null) == "0.8.0" ]] && pass "running v0.8.0 (Device Identifier included)" \
  || fail "gateway runs $(jq -r .version <<<"$h" 2>/dev/null), expected 0.8.0" "sudo systemctl restart open5gs-camara"

gnb=$(curl -fsS -m3 http://127.0.0.5:9090/gnb-info 2>/dev/null | jq '[.items[]? | select(.ng.setup_success)] | length')
[[ ${gnb:-0} -ge 1 ]] && pass "gNB connected to AMF ($gnb)" || fail "no gNB on N2" "start nr-gnb on the UERANSIM host"

ue=$(curl -fsS -m3 http://127.0.0.5:9090/ue-info 2>/dev/null | jq -c --arg m "${PHONE#+}" '[.items[] | select((.msisdn // []) | index($m))][0]')
if [[ -n $ue && $ue != null ]]; then
  cm=$(jq -r .cm_state <<<"$ue"); [[ $cm == connected ]] && pass "UE for $PHONE registered, CM-CONNECTED" \
    || fail "UE for $PHONE is CM-$cm" "send traffic from the UE (ping -I uesimtun0 10.45.0.1) or restart nr-ue"
else fail "UE for $PHONE not known to the AMF" "start/restart nr-ue on the UERANSIM host"; fi

ip=$(curl -fsS -m3 http://127.0.0.4:9090/pdu-info 2>/dev/null | jq -r --arg s "$(jq -r .supi <<<"${ue:-null}" 2>/dev/null)" '.items[] | select(.supi==$s) | .pdu[0].ipv4' | head -1)
if [[ -n $ip && $ip != null ]]; then
  pass "PDU session up, UE IP $ip"
  ping -c 2 -W 2 "$ip" >/dev/null 2>&1 && pass "UE answers ping over ogstun (Connectivity Insights works)" \
    || fail "UE does not answer ping" "re-attach the UE (restart nr-ue); a UE idle after a gNB restart is not reachable"
  b=$(curl -s -o /dev/null -w '%{http_code}' --http2-prior-knowledge "http://127.0.0.15:7777/nbsf-management/v1/pcfBindings?ipv4Addr=$ip")
  [[ $b == 200 ]] && pass "BSF has a PCF binding for $ip (QoD can find the PCF)" || fail "no BSF binding for $ip" "re-establish the PDU session"
else fail "no PDU session for the UE" "restart nr-ue"; fi

TOKEN=$(token "device-swap:check sim-swap:check" 2>/dev/null)
sw=$(curl -fsS -X POST "$CAMARA_URL/device-swap/v1/check" -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' -d "{\"phoneNumber\":\"$PHONE\",\"maxAge\":24}" | jq -r .swapped)
[[ $sw == false ]] && pass "no device swap recorded in 24 h (Story 1 starts clean)" \
  || fail "device swap recorded in last 24 h (swapped=$sw)" "if it's demo residue: .venv/bin/python tools/lab_swap.py reset-history"
ss -ltn | grep -q ':9099 ' && pass "event sink listening on :9099" || fail "no event sink on :9099" "python3 demo/sink.py   (in its own terminal)"

echo; echo "$ok passed, $bad to fix"; [[ $bad -eq 0 ]]
