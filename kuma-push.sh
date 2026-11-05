#!/usr/bin/env bash
# Report Open5GS 5GC health to an Uptime Kuma Push monitor.
#
# Config: ~/.config/open5gs-kuma.env (mode 600, not in git), containing:
#   KUMA_PUSH_URL="https://192.168.64.4/api/push/<TOKEN>"
#   KUMA_CACERT="/etc/ssl/certs/uptime-kuma.crt"   # optional, this is the default
#
# Cron (every minute):
#   * * * * * $HOME/open5gs/kuma-push.sh >/dev/null 2>&1
#
# Checks:
#   - every required NF type is REGISTERED in NRF (SBI is h2c-only)
#   - AMF NGAP is listening (SCTP 38412)
#   - UPF metrics endpoint answers (UPF doesn't register with NRF)
set -uo pipefail

CONF="${OPEN5GS_KUMA_ENV:-$HOME/.config/open5gs-kuma.env}"
[[ -r $CONF ]] || { echo "missing $CONF" >&2; exit 2; }
# shellcheck source=/dev/null
. "$CONF"
: "${KUMA_PUSH_URL:?KUMA_PUSH_URL not set in $CONF}"
KUMA_CACERT="${KUMA_CACERT:-/etc/ssl/certs/uptime-kuma.crt}"

NRF="http://127.0.0.10:7777/nnrf-nfm/v1/nf-instances"
REQUIRED_NFS=(NRF SCP AMF SMF AUSF UDM PCF NSSF BSF UDR)
NGAP_PORT=38412
UPF_METRICS="http://127.0.0.7:9090/metrics"
AMF_METRICS="http://127.0.0.5:9090/metrics"

push() {  # push <up|down> <msg> [ping]
    local status=$1 msg=$2 ping=${3:-}
    curl -fsS -m10 --cacert "$KUMA_CACERT" -G "$KUMA_PUSH_URL" \
        --data-urlencode "status=$status" \
        --data-urlencode "msg=$msg" \
        --data-urlencode "ping=$ping" >/dev/null \
        || { echo "push to Kuma failed" >&2; exit 3; }
}
fail() { echo "DOWN: $1" >&2; push down "$1"; exit 1; }

start=$(date +%s%3N)

# 1. NF registrations in NRF
hrefs=$(curl -fsS -m3 --http2-prior-knowledge "$NRF" | jq -r '._links.item[]?.href') \
    || fail "NRF unreachable"
registered=" "
for h in $hrefs; do
    t=$(curl -fsS -m3 --http2-prior-knowledge "$h" \
        | jq -r 'select(.nfStatus=="REGISTERED") | .nfType') || continue
    registered+="$t "
done
missing=()
for nf in "${REQUIRED_NFS[@]}"; do
    [[ $registered == *" $nf "* ]] || missing+=("$nf")
done
(( ${#missing[@]} == 0 )) || fail "not registered in NRF: ${missing[*]}"

# 2. AMF NGAP (SCTP)
ss -lnA sctp 2>/dev/null | grep -q ":$NGAP_PORT\b" || fail "AMF NGAP (SCTP $NGAP_PORT) not listening"

# 3. UPF alive
curl -fsS -m3 "$UPF_METRICS" >/dev/null || fail "UPF metrics unreachable"

gnb=$(curl -fsS -m3 "$AMF_METRICS" | awk '$1=="gnb"{print $2}')
ue=$(curl -fsS -m3 "$AMF_METRICS" | awk '$1=="ran_ue"{print $2}')
elapsed=$(( $(date +%s%3N) - start ))

push up "OK: ${#REQUIRED_NFS[@]} NFs registered, UPF up, gNB=${gnb:-?} UE=${ue:-?}" "$elapsed"
echo "UP (${elapsed}ms) gNB=${gnb:-?} UE=${ue:-?}"
