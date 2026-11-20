#!/usr/bin/env bash
# Report open5gs-camara gateway health to its own Uptime Kuma Push monitor
# (kept separate from ../../kuma-push.sh so a demo-gateway outage doesn't mark the 5GC down).
#
# Config: ~/.config/open5gs-camara-kuma.env (mode 600, not in git):
#   KUMA_PUSH_URL="https://192.168.64.4/api/push/<TOKEN>"
#   KUMA_CACERT="/etc/ssl/certs/uptime-kuma.crt"   # optional, this is the default
#   CAMARA_URL="http://127.0.0.1:8090"              # optional
# Cron:  * * * * * $HOME/open5gs/misc/camara/kuma-push-camara.sh >/dev/null 2>&1
set -uo pipefail
CONF="${OPEN5GS_CAMARA_KUMA_ENV:-$HOME/.config/open5gs-camara-kuma.env}"
[[ -r $CONF ]] || { echo "missing $CONF" >&2; exit 2; }
# shellcheck source=/dev/null
. "$CONF"
: "${KUMA_PUSH_URL:?KUMA_PUSH_URL not set in $CONF}"
KUMA_CACERT="${KUMA_CACERT:-/etc/ssl/certs/uptime-kuma.crt}"
CAMARA_URL="${CAMARA_URL:-http://127.0.0.1:8090}"

push() {  # push <up|down> <msg> [ping]
  curl -fsS -m10 --cacert "$KUMA_CACERT" -G "$KUMA_PUSH_URL" \
    --data-urlencode "status=$1" --data-urlencode "msg=$2" ${3:+--data-urlencode "ping=$3"} >/dev/null
}

start=$(date +%s%N)
body=$(curl -sS -m5 "$CAMARA_URL/health" 2>&1) || { push down "gateway unreachable: ${body:0:120}"; exit 1; }
ms=$(( ($(date +%s%N) - start) / 1000000 ))
status=$(jq -r '.status // "?"' <<<"$body" 2>/dev/null)
if [[ $status == UP ]]; then
  push up "camara $(jq -r .version <<<"$body") UP" "$ms"
else
  failed=$(jq -r '[.checks | to_entries[] | select(.value.ok|not) | .key] | join(",")' <<<"$body" 2>/dev/null)
  push down "camara DOWN: ${failed:-unknown}" "$ms"; exit 1
fi
