#!/usr/bin/env bash
# All three POC user stories in one go: pre-flight → Story 1 Identity → Story 2 Location → Story 3 Quality.
#
#   ./misc/camara/demo/stories.sh                     # step-by-step (Enter between steps)
#   UE_SSH='ssh user@192.168.64.3' ./misc/camara/demo/stories.sh   # Story 1 device step over SSH (else paste the redirect)
#   ONLY="1 2" ./misc/camara/demo/stories.sh          # run a subset
#   PAUSE=0 ./misc/camara/demo/stories.sh             # no pauses (scripted)
#
# Needs the event sink running in another terminal:  python3 misc/camara/demo/sink.py
# Story 3 runs last and asks first: with UERANSIM the gNB drops the UE when the QoS flow is installed.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
export CAMARA_URL="${CAMARA_URL:-http://localhost:8090}" PAUSE="${PAUSE:-1}"
ONLY="${ONLY:-1 2 3}"
want() { [[ " $ONLY " == *" $1 "* ]]; }
banner() { echo; printf '\033[1;7m  %s  \033[0m\n' "$*"; }

banner "Pre-flight"
"$HERE/preflight.sh" || { echo; read -r -p "Pre-flight has failures. Continue anyway? [y/N] " a </dev/tty; [[ $a == [yY] ]] || exit 1; }

if want 1; then banner "Story 1 · Identity — the cloned agent can't act"; "$HERE/story1-identity.sh" || echo "(story 1 ended with an error)"; fi
if want 2; then banner "Story 2 · Location — used from an approved country?"; "$HERE/story2-location.sh" || echo "(story 2 ended with an error)"; fi
if want 3; then
  banner "Story 3 · Quality — priority only when the tool needs it"
  echo "⚠ With UERANSIM, installing the QoS flow makes the gNB drop the UE (restart nr-gnb + nr-ue afterwards)."
  if [[ $PAUSE == 1 ]]; then read -r -p "Run Story 3 now? [y/N] " a </dev/tty; [[ $a == [yY] ]] || { echo "Story 3 skipped."; exit 0; }; fi
  "$HERE/story3-quality.sh" || echo "(story 3 ended with an error)"
fi
banner "Done — events received are in the sink terminal"
