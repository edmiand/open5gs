#!/usr/bin/env bash
# Phase 1: QoS Profiles + Quality on Demand.
#
#   ./demo/sink.py &                      # in another terminal: watch CloudEvents
#   PHONE=+99970000001 AS=198.51.100.10 PROFILE=QOS_E DURATION=120 ./demo/01-qod.sh
#
# Steps: list profiles -> create session -> poll until AVAILABLE -> show the dedicated QoS
# flow in SMF /pdu-info -> extend -> delete (KEEP=1 skips the delete).
. "$(dirname "$0")/lib.sh"
PHONE="${PHONE:-+99970000001}"
AS="${AS:-198.51.100.10}"
PROFILE="${PROFILE:-QOS_E}"
DURATION="${DURATION:-120}"
SINK="${SINK:-http://127.0.0.1:9099/events}"
SMF_INFO="${SMF_INFO:-http://127.0.0.4:9090/pdu-info}"

TOKEN=$(token "qos-profiles:read quality-on-demand:sessions:create quality-on-demand:sessions:read quality-on-demand:sessions:update quality-on-demand:sessions:delete quality-on-demand:sessions:retrieve-by-device")

flows() { curl -fsS "$SMF_INFO" | jq -c '.items[] | {supi, qos_flows: [.pdu[].qos_flows[]]}'; }

echo "== QoS profiles"
call POST /qos-profiles/v1/retrieve-qos-profiles '{"status":"ACTIVE"}' | jq -c '.[]? | {name, maxDownstreamRate, packetDelayBudget}' 2>/dev/null || true

echo "== QoS flows BEFORE"; flows

echo "== create QoD session ($PROFILE, $DURATION s, AS $AS)"
resp=$(curl -fsS -X POST "$CAMARA_URL/quality-on-demand/v1/sessions" -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d "{\"device\":{\"phoneNumber\":\"$PHONE\"},\"applicationServer\":{\"ipv4Address\":\"$AS\"},\"qosProfile\":\"$PROFILE\",\"duration\":$DURATION,\"sink\":\"$SINK\"}") \
  || { echo "create failed:"; call POST /quality-on-demand/v1/sessions "{\"device\":{\"phoneNumber\":\"$PHONE\"},\"applicationServer\":{\"ipv4Address\":\"$AS\"},\"qosProfile\":\"$PROFILE\",\"duration\":$DURATION}"; exit 1; }
echo "$resp" | jq .
SID=$(echo "$resp" | jq -r .sessionId)

echo "== waiting for AVAILABLE"
for i in $(seq 1 30); do
  st=$(curl -fsS "$CAMARA_URL/quality-on-demand/v1/sessions/$SID" -H "Authorization: Bearer $TOKEN" | jq -r '.qosStatus // "GONE"' 2>/dev/null || echo GONE)
  echo "  $st"; [[ $st == REQUESTED ]] || break; sleep 1
done
call GET "/quality-on-demand/v1/sessions/$SID"

echo "== QoS flows AFTER"; flows

echo "== extend by 60 s"; call POST "/quality-on-demand/v1/sessions/$SID/extend" '{"requestedAdditionalDuration":60}'
echo "== retrieve sessions by device"; call POST /quality-on-demand/v1/retrieve-sessions "{\"device\":{\"phoneNumber\":\"$PHONE\"}}"

if [[ ${KEEP:-0} != 1 ]]; then
  echo "== delete"; call DELETE "/quality-on-demand/v1/sessions/$SID"
  sleep 2; echo "== QoS flows after delete"; flows
fi
