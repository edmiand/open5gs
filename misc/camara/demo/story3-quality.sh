#!/usr/bin/env bash
# Story 3 — Quality: "priority only when the tool needs it".
# When the agent opens the live-video tool, request priority for the device↔video-service link for a limited time.
#
#   ./demo/sink.py &          # AVAILABLE / UNAVAILABLE confirmations
#   ./demo/story3-quality.sh  # VIDEO_AS, PROFILE (QOS_M), DURATION (s, default 1800); PAUSE=0 scripted
#
# ⚠ With UERANSIM the gNB drops N2 when the QoS flow is pushed to the radio (PDUSessionResourceModifyRequest).
#   The flow is real in the core (PCF/SMF); restart the gNB and re-attach the UE afterwards. Run this story LAST.
. "$(dirname "$0")/lib.sh"
PHONE="${PHONE:-+99970000001}"
VIDEO_AS="${VIDEO_AS:-198.51.100.10}"
PROFILE="${PROFILE:-QOS_M}"; DURATION="${DURATION:-1800}"
SINK="${SINK:-http://127.0.0.1:9099/events}"
SMF_INFO="${SMF_INFO:-http://127.0.0.4:9090/pdu-info}"
TOKEN=$(token "qos-profiles:read quality-on-demand:sessions:create quality-on-demand:sessions:read quality-on-demand:sessions:delete application-profiles:create application-profiles:delete connectivity-insights:check")
flows() { curl -fsS "$SMF_INFO" | jq -c '.items[] | {supi, qos_flows: [.pdu[].qos_flows[]]}'; }

step "1 · Text tools: no priority requested — current QoS flows (SMF)"; flows
step "2 · (optional) Connectivity Insights: can the network meet a live-video profile right now?"
post_json /application-profiles/v0.6/application-profiles '{"networkQualityThresholds":{"packetDelayBudget":{"value":100,"unit":"Milliseconds"},"jitter":{"value":30,"unit":"Milliseconds"},"packetLossErrorRate":2}}' >/dev/null
APP=$(jq -r .applicationProfileId <<<"$LAST")
post_json /connectivity-insights/v0.7/check-network-quality "{\"applicationProfileId\":\"$APP\",\"device\":{\"phoneNumber\":\"$PHONE\"},\"applicationServer\":{\"ipv4Address\":\"$VIDEO_AS\"}}"
step "3 · Which priority profiles does the network offer?"
post_json /qos-profiles/v1/retrieve-qos-profiles '{"status":"ACTIVE"}' >/dev/null
jq -c '.[] | {name, maxDownstreamRate, targetMinDownstreamRate, packetDelayBudget, maxDuration}' <<<"$LAST"
step "4 · Agent opens the live-video tool → request $PROFILE for $DURATION s to $VIDEO_AS"
post_json /quality-on-demand/v1/sessions "{\"device\":{\"phoneNumber\":\"$PHONE\"},\"applicationServer\":{\"ipv4Address\":\"$VIDEO_AS\"},\"qosProfile\":\"$PROFILE\",\"duration\":$DURATION,\"sink\":\"$SINK\"}"
SID=$(jq -r .sessionId <<<"$LAST")
step "5 · Network confirms priority is active"
for i in $(seq 1 15); do st=$(curl -fsS "$CAMARA_URL/quality-on-demand/v1/sessions/$SID" -H "Authorization: Bearer $TOKEN" | jq -r .qosStatus); note "$st"; [[ $st == REQUESTED ]] || break; sleep 1; done
flows; fact "a dedicated QoS flow (extra QFI) now carries the video traffic"
step "6 · Video tool closed → release priority"
call DELETE "/quality-on-demand/v1/sessions/$SID" | tail -1; sleep 2; flows
call DELETE "/application-profiles/v0.6/application-profiles/$APP" >/dev/null 2>&1 || true
note "(sink: AVAILABLE when priority started, UNAVAILABLE/DELETE_REQUESTED when it ended)"
