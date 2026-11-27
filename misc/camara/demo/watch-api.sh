#!/usr/bin/env bash
# Live view of CAMARA gateway traffic: one line per API call + gateway events (camara.*) + warnings/errors.
#
#   ./demo/watch-api.sh                 # follow live (hides /health polling)
#   ./demo/watch-api.sh --all           # include /health
#   SINCE="1 hour ago" ./demo/watch-api.sh   # replay the last hour first, then follow
#   ./demo/watch-api.sh --no-follow     # print SINCE..now and exit
#
# Source column: "public" = came in through Tailscale Funnel (proxied from 127.0.0.1),
# otherwise the caller's lab IP (e.g. 192.168.64.7 = Guardian gateway, 10.45.x.x = a UE).
# Needs read access to the journal (user in group systemd-journal or adm), like `journalctl -u open5gs-camara`.
set -euo pipefail

UNIT="${UNIT:-open5gs-camara}"
SINCE="${SINCE:-now}"
SHOW_HEALTH=0; FOLLOW=(-f)
for a in "$@"; do
  case "$a" in
    --all) SHOW_HEALTH=1 ;;
    --no-follow) FOLLOW=() ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done
[ "${#FOLLOW[@]}" -eq 0 ] && [ "$SINCE" = now ] && SINCE="1 hour ago"

if [ -t 1 ]; then R=$'\e[31m' G=$'\e[32m' Y=$'\e[33m' C=$'\e[36m' D=$'\e[2m' N=$'\e[0m'; else R= G= Y= C= D= N=; fi

printf '%s%-8s %-15s %-6s %-52s %s%s\n' "$D" TIME SOURCE METHOD PATH STATUS "$N"
journalctl -u "$UNIT" -o cat --since "$SINCE" "${FOLLOW[@]}" --no-pager | \
awk -v show_health="$SHOW_HEALTH" -v R="$R" -v G="$G" -v Y="$Y" -v C="$C" -v D="$D" -v N="$N" '
  # Hypercorn access log:
  # [2026-11-26 16:14:59 +0000] [16683] [INFO] 127.0.0.1:45582 - - [..] "POST /path 1.1" 200 108 "-" "curl/7.81.0"
  /\] \[INFO\] [0-9a-fA-F.:]+ - - \[/ && /"[A-Z]+ \// {
    time = substr($2, 1, 8)
    src = $6; sub(/:[0-9]+$/, "", src); if (src == "127.0.0.1") src = "public"
    match($0, /"[A-Z]+ [^ ]+ [0-9.]+"/); req = substr($0, RSTART + 1, RLENGTH - 2)
    split(req, q, " "); method = q[1]; path = q[2]
    rest = substr($0, RSTART + RLENGTH); split(rest, s, " "); status = s[1]
    if (!show_health && path == "/health") next
    col = (status >= 500) ? R : (status >= 400) ? Y : G
    printf "%-8s %-15s %-6s %-52s %s%s%s\n", time, src, method, path, col, status, N
    fflush(); next
  }
  # gateway events, e.g. "2026-11-26 00:43:18,400 INFO camara.swap: DEVICE_SWAP ..."
  /^[0-9-]+ [0-9:,]+ [A-Z]+ camara\./ {
    time = substr($2, 1, 8); lvl = $3; $1 = $2 = $3 = ""; sub(/^ +/, "")
    col = (lvl == "ERROR") ? R : (lvl == "WARNING") ? Y : C
    printf "%s%-8s %-15s %s%s\n", col, time, "event", $0, N; fflush(); next
  }
  # anything else at warning/error level, and tracebacks
  /WARNING|ERROR|Traceback|Exception/ { printf "%s%s%s\n", R, $0, N; fflush() }
'
