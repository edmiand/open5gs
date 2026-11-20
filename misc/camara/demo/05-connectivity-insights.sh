#!/usr/bin/env bash
# Phase 5: Application Profiles + Connectivity Insights.
#
#   ./demo/05-connectivity-insights.sh            # two profiles (video-call = lenient, cloud-gaming = strict), check both
#   NETEM=1 ./demo/05-connectivity-insights.sh    # also degrade ogstun with tc netem (sudo) and re-check, then restore
#
# Probes: ICMP from the VM over ogstun to the UE (RTT/2 = one-way delay, p99 delay variation = jitter, loss).
# Throughput KPIs are only assessed when ci.iperf_enabled (iperf3 on VM + `iperf3 -s` on UE host).
. "$(dirname "$0")/lib.sh"
PHONE="${PHONE:-+99970000001}"
AS="${AS:-198.51.100.10}"
NETEM_ARGS="${NETEM_ARGS:-delay 40ms 15ms loss 5%}"
TOKEN=$(token "application-profiles:create application-profiles:read application-profiles:delete connectivity-insights:check lab:read")

mk() { curl -fsS -X POST "$CAMARA_URL/application-profiles/v0.6/application-profiles" -H "Authorization: Bearer $TOKEN" \
        -H 'content-type: application/json' -d "{\"networkQualityThresholds\":$1}" | jq -r .applicationProfileId; }
chk() {
  echo "-- check: $1"
  call POST /connectivity-insights/v0.7/check-network-quality \
    "{\"applicationProfileId\":\"$2\",\"device\":{\"phoneNumber\":\"$PHONE\"},\"applicationServer\":{\"ipv4Address\":\"$AS\"}}"
  curl -fsS "$CAMARA_URL/_lab/connectivity-insights/last" -H "Authorization: Bearer $TOKEN" \
    | jq -c '.measured | {oneWayDelayMs: (.oneWayDelayMs // null | if . then (.*100|round/100) else . end), jitterMs: (.jitterMs // null | if . then (.*100|round/100) else . end), lossRate, pingReceived, pingSent}'
}

echo "== application profiles"
VIDEO=$(mk '{"packetDelayBudget":{"value":50,"unit":"Milliseconds"},"jitter":{"value":30,"unit":"Milliseconds"},"packetLossErrorRate":1}')
GAMING=$(mk '{"packetDelayBudget":{"value":10,"unit":"Milliseconds"},"jitter":{"value":5,"unit":"Milliseconds"},"packetLossErrorRate":2}')
echo "   video-call   = $VIDEO  (PDB 50 ms, jitter 30 ms, loss <= 10^-1)"
echo "   cloud-gaming = $GAMING  (PDB 10 ms, jitter 5 ms,  loss <= 10^-2)"

chk "video-call (normal network)" "$VIDEO"
chk "cloud-gaming (normal network)" "$GAMING"

if [[ ${NETEM:-0} == 1 ]]; then
  echo "== degrade: sudo tc qdisc add dev ogstun root netem $NETEM_ARGS"
  sudo tc qdisc add dev ogstun root netem $NETEM_ARGS
  trap 'sudo tc qdisc del dev ogstun root 2>/dev/null; echo "== netem removed"' EXIT
  chk "video-call (degraded)" "$VIDEO"
  chk "cloud-gaming (degraded)" "$GAMING"
fi

echo "== cleanup profiles"
for p in "$VIDEO" "$GAMING"; do call DELETE "/application-profiles/v0.6/application-profiles/$p" | tail -1; done
