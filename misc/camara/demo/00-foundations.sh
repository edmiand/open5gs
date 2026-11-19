#!/usr/bin/env bash
# Phase 0: health, token, device resolution.
#   PHONE=+99970000001 UE_IP=10.45.0.2 ./demo/00-foundations.sh
. "$(dirname "$0")/lib.sh"
PHONE="${PHONE:-+99970000001}"

echo "== health"; curl -sS "$CAMARA_URL/health" | jq .
echo "== token (client_credentials)"; TOKEN=$(token lab:read); echo "${TOKEN:0:32}…"
echo "== resolve by phone number"; call POST /_lab/resolve "{\"device\":{\"phoneNumber\":\"$PHONE\"}}"
if [[ -n ${UE_IP:-} ]]; then
    echo "== resolve by UE IPv4"; call POST /_lab/resolve "{\"device\":{\"ipv4Address\":{\"privateAddress\":\"$UE_IP\"}}}"
fi
echo "== error example: unknown number"; call POST /_lab/resolve '{"device":{"phoneNumber":"+15550000000"}}'
