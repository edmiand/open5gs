#!/usr/bin/env bash
# Full CAMARA demo walkthrough against the lab gateway.
#
#   ./demo/run-all.sh                 # all steps, non-interactive
#   PAUSE=1 ./demo/run-all.sh         # wait for Enter between steps (presenting)
#   UE_SSH='ssh user@ueransim-host' ./demo/run-all.sh   # include Number Verification (device step over SSH)
#   NETEM=1 ./demo/run-all.sh         # include the degraded-network Connectivity Insights step (sudo tc)
#   CAMARA_URL=http://localhost:8091 ./demo/run-all.sh   # another gateway instance
#   SKIP="1" ./demo/run-all.sh        # skip steps by number (e.g. QoD with UERANSIM, whose gNB drops N2 on QoS-flow modify)
#
# Starts demo/sink.py on :9099 (unless something already listens there) so events are visible.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
export CAMARA_URL="${CAMARA_URL:-http://localhost:8090}"
SINK_LOG="${SINK_LOG:-/tmp/camara-sink-$USER.log}"

step() {
  local n=${1%%.*}
  echo; echo "████ $1"
  if [[ " ${SKIP:-} " == *" $n "* ]]; then echo "   (skipped via SKIP)"; return 1; fi
  if [[ ${PAUSE:-0} == 1 ]]; then read -r -p "   [Enter] to run, or 's' to skip: " a; [[ $a == s ]] && return 1; fi
  return 0
}

sink_pid=""
if ! ss -ltn 2>/dev/null | grep -q ':9099 '; then
  python3 -u "$HERE/sink.py" > "$SINK_LOG" 2>&1 & sink_pid=$!
  echo "CloudEvents sink started (pid $sink_pid), log: $SINK_LOG   (tail -f it in another terminal)"
fi
trap '[[ -n $sink_pid ]] && kill $sink_pid 2>/dev/null; true' EXIT

curl -fsS "$CAMARA_URL/health" | jq -e '.status == "UP"' >/dev/null || { echo "gateway at $CAMARA_URL is not UP"; exit 1; }

step "0. Foundations — health, token, device resolution"          && "$HERE/00-foundations.sh" || true
step "1. QoS Profiles + Quality on Demand"                         && PROFILE=QOS_E DURATION=120 "$HERE/01-qod.sh" || true
step "2. SIM Swap + Device Swap + SIM Swap Subscriptions"          && "$HERE/02-swap.sh" || true
if [[ -n ${UE_SSH:-} ]]; then
  step "3. Number Verification (network-based auth via $UE_SSH)"   && "$HERE/03-number-verification.sh" || true
else
  echo; echo "████ 3. Number Verification — skipped (set UE_SSH, or run ./demo/03-number-verification.sh interactively)"
fi
step "4. Device Roaming Status + subscriptions (lab override)"     && "$HERE/04-roaming.sh" || true
step "5. Application Profiles + Connectivity Insights"             && "$HERE/05-connectivity-insights.sh" || true

if [[ -n $sink_pid ]]; then
  echo; echo "████ Events received by the sink:"; grep -E "🔔" "$SINK_LOG" | sed 's/^/   /' || true
fi
