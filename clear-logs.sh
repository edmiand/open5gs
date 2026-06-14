#!/usr/bin/env bash
# Truncate all Open5GS NF log files in-place.
# Truncating (not deleting) preserves open file descriptors held by running
# processes — the space is freed immediately without needing a restart.

set -euo pipefail

LOGDIR="$(cd "$(dirname "$0")/install/var/log/open5gs" && pwd)"

total_freed=0

truncate_log() {
    local f=$1
    local size_kb; size_kb=$(du -k "$f" | cut -f1)
    if [[ $(stat -c '%U' "$f") == "root" ]]; then
        sudo truncate -s 0 "$f"
    else
        truncate -s 0 "$f"
    fi
    printf "  cleared %-16s (%d KB freed)\n" "$(basename "$f")" "$size_kb"
    total_freed=$(( total_freed + size_kb ))
}

echo "Clearing Open5GS logs in $LOGDIR ..."
echo ""

for f in "$LOGDIR"/*.log; do
    [[ -f $f ]] && truncate_log "$f"
done

echo ""
printf "Done. Total freed: %d KB\n" "$total_freed"
