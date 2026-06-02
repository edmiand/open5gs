#!/usr/bin/env bash
# Control script for Open5GS 5GC (prefix install at ~/open5gs/install/)

INSTALL_DIR="$(cd "$(dirname "$0")/install" && pwd)"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN="$INSTALL_DIR/bin"
LOGDIR="$INSTALL_DIR/var/log/open5gs"
RUNDIR="$INSTALL_DIR/var/run/open5gs"

# 5GC NFs in dependency order
NFS_5GC=(nrf scp amf smf upf ausf udm pcf nssf bsf udr)
# NFs that must run as root (need TUN/raw sockets)
NFS_ROOT=(upf)

# WebUI
WEBUI_DIR="$SCRIPT_DIR/webui"
WEBUI_PORT=9999
# Auto-detect primary external IP (same logic used when configs were set up)
WEBUI_HOSTNAME=$(ip addr show | awk '/inet / && !/127\./ && !/ogstun/{gsub(/\/.*/, "", $2); print $2; exit}')

# ── helpers ────────────────────────────────────────────────────────────────────

needs_root() { local nf=$1; [[ " ${NFS_ROOT[*]} " == *" $nf "* ]]; }

pidfile() { echo "$RUNDIR/$1.pid"; }

# Return PID of a running NF, or empty string.
# Checks pidfile first, then falls back to pgrep by process name.
# Uses /proc to check liveness — avoids kill -0 permission failures for
# root-owned processes (UPF) and avoids -f/-n pgrep edge cases.
get_pid() {
    local nf=$1 pid=""

    # Check pidfile
    local pf; pf=$(pidfile "$nf")
    if [[ -f $pf ]]; then
        pid=$(cat "$pf" 2>/dev/null)
        if [[ -n $pid ]] && [[ -d /proc/$pid ]]; then
            echo "$pid"; return 0
        fi
        rm -f "$pf"
        pid=""
    fi

    # Fall back to pgrep by process name (no -f, so it matches comm, not cmdline)
    # This works for both relative- and absolute-path launches, and won't match
    # shell wrappers that happen to mention the binary name in their args.
    pid=$(pgrep "open5gs-${nf}d" 2>/dev/null || true)
    # pgrep can return multiple PIDs; take the first valid one
    local p
    for p in $pid; do
        [[ -d /proc/$p ]] && echo "$p" && return 0
    done
    echo ""
}

proc_uptime_str() {
    local pid=$1
    [[ -f /proc/$pid/stat ]] || { echo ""; return; }
    local starttime hz now_ticks elapsed
    starttime=$(awk '{print $22}' /proc/"$pid"/stat 2>/dev/null) || return
    hz=$(getconf CLK_TCK 2>/dev/null) || hz=100
    now_ticks=$(awk '{printf "%d", $1 * '"$hz"'}' /proc/uptime 2>/dev/null) || return
    elapsed=$(( (now_ticks - starttime) / hz ))
    if   (( elapsed >= 3600 )); then printf "%dh%02dm" $((elapsed/3600)) $(( (elapsed%3600)/60 ))
    elif (( elapsed >= 60   )); then printf "%dm%02ds"  $((elapsed/60))   $((elapsed%60))
    else printf "%ds" "$elapsed"
    fi
}

# ── webui ──────────────────────────────────────────────────────────────────────

webui_get_pid() {
    local pf; pf=$(pidfile "webui")
    if [[ -f $pf ]]; then
        local pid; pid=$(cat "$pf" 2>/dev/null)
        if [[ -n $pid ]] && [[ -d /proc/$pid ]]; then
            echo "$pid"; return 0
        fi
        rm -f "$pf"
    fi
    # Fallback: find a node process running from this repo's webui dir
    local p
    for p in $(pgrep -f "$WEBUI_DIR" 2>/dev/null || true); do
        [[ -d /proc/$p ]] && echo "$p" && return 0
    done
    # Final fallback: find node process listening on WEBUI_PORT (handles
    # relative-path launches where cmdline doesn't contain WEBUI_DIR)
    for p in $(ss -tlnp "sport = :$WEBUI_PORT" 2>/dev/null \
               | grep -oP 'pid=\K[0-9]+' || true); do
        [[ -d /proc/$p ]] && echo "$p" && return 0
    done
    echo ""
}

webui_start() {
    local pid; pid=$(webui_get_pid)
    if [[ -n $pid ]]; then
        echo "  webui: already running (pid $pid)"; return 0
    fi
    if [[ ! -d $WEBUI_DIR/node_modules ]]; then
        echo "  webui: ERROR — node_modules missing, run: cd webui && npm ci"; return 1
    fi
    mkdir -p "$RUNDIR"
    HOSTNAME="$WEBUI_HOSTNAME" npm run dev --prefix "$WEBUI_DIR" \
        >> "$LOGDIR/webui.log" 2>&1 &
    local bgpid=$!
    sleep 3
    # npm forks; find the actual node server process
    pid=$(webui_get_pid)
    if [[ -n $pid ]]; then
        echo "$pid" > "$(pidfile "webui")"
        echo "  webui: started — http://${WEBUI_HOSTNAME}:${WEBUI_PORT} (pid $pid)"
    else
        echo "  webui: ERROR — failed to start, check $LOGDIR/webui.log"; return 1
    fi
}

webui_stop() {
    local pid; pid=$(webui_get_pid)
    if [[ -z $pid ]]; then
        echo "  webui: not running"
        rm -f "$(pidfile "webui")"
        return 0
    fi
    # Kill the whole process group so npm + node children all die
    kill -- -"$(ps -o pgid= -p "$pid" | tr -d ' ')" 2>/dev/null \
        || kill "$pid" 2>/dev/null || true
    local i=0
    while [[ -d /proc/$pid ]] && (( i < 20 )); do sleep 0.5; (( i++ )); done
    rm -f "$(pidfile "webui")"
    echo "  webui: stopped"
}

# ── actions ────────────────────────────────────────────────────────────────────

start_one() {
    local nf=$1
    local pid; pid=$(get_pid "$nf")
    if [[ -n $pid ]]; then
        echo "  $nf: already running (pid $pid)"; return 0
    fi

    local bin="$BIN/open5gs-${nf}d"
    if [[ ! -x $bin ]]; then
        echo "  $nf: ERROR — binary not found: $bin"; return 1
    fi

    mkdir -p "$RUNDIR"
    if needs_root "$nf"; then
        sudo "$bin" -D
    else
        "$bin" -D
    fi
    sleep 1.5

    pid=$(get_pid "$nf")
    if [[ -n $pid ]]; then
        echo "$pid" > "$(pidfile "$nf")" 2>/dev/null || true
        echo "  $nf: started (pid $pid)"
    else
        echo "  $nf: ERROR — failed to start, check $LOGDIR/${nf}.log"; return 1
    fi
}

stop_one() {
    local nf=$1
    local pid; pid=$(get_pid "$nf")

    if [[ -z $pid ]]; then
        echo "  $nf: not running"
        rm -f "$(pidfile "$nf")"
        return 0
    fi

    if needs_root "$nf"; then
        sudo kill "$pid" 2>/dev/null || true
    else
        kill "$pid" 2>/dev/null || true
    fi

    local i=0
    while [[ -d /proc/$pid ]] && (( i < 20 )); do
        sleep 0.5; (( i++ ))
    done
    if [[ -d /proc/$pid ]]; then
        echo "  $nf: did not stop cleanly, sending SIGKILL"
        needs_root "$nf" && sudo kill -9 "$pid" 2>/dev/null || kill -9 "$pid" 2>/dev/null || true
    fi

    rm -f "$(pidfile "$nf")"
    echo "  $nf: stopped"
}

dispatch_start() { if [[ $1 == webui ]]; then webui_start; else start_one "$1"; fi; }
dispatch_stop()  { if [[ $1 == webui ]]; then webui_stop;  else stop_one  "$1"; fi; }

do_start() {
    local -a targets=("${@:-${NFS_5GC[@]} webui}")
    mkdir -p "$RUNDIR"
    echo "Starting Open5GS 5GC..."
    local t; for t in "${targets[@]}"; do dispatch_start "$t"; done
    echo "Done."
}

do_stop() {
    local -a targets
    if [[ $# -gt 0 ]]; then
        targets=("$@")
    else
        # reverse NF order, webui first (it has no NF dependents)
        targets=(webui)
        local i; for (( i=${#NFS_5GC[@]}-1; i>=0; i-- )); do
            targets+=("${NFS_5GC[$i]}")
        done
    fi
    echo "Stopping Open5GS 5GC..."
    local t; for t in "${targets[@]}"; do dispatch_stop "$t"; done
    echo "Done."
}

do_restart() {
    local -a targets=("${@:-${NFS_5GC[@]} webui}")
    echo "Restarting Open5GS 5GC..."
    local -a rev=()
    local i; for (( i=${#targets[@]}-1; i>=0; i-- )); do rev+=("${targets[$i]}"); done
    local t
    for t in "${rev[@]}"; do dispatch_stop "$t"; done
    sleep 1
    for t in "${targets[@]}"; do dispatch_start "$t"; done
    echo "Done."
}

do_status() {
    local -a targets=("${@:-${NFS_5GC[@]} webui}")
    printf "\n%-8s %-10s %-8s %s\n" "NF" "STATUS" "PID" "UPTIME"
    printf "%-8s %-10s %-8s %s\n"   "--------" "----------" "--------" "-------"
    local t
    for t in "${targets[@]}"; do
        local pid
        if [[ $t == webui ]]; then
            pid=$(webui_get_pid)
        else
            pid=$(get_pid "$t")
        fi
        if [[ -n $pid ]] && [[ -d /proc/$pid ]]; then
            local up; up=$(proc_uptime_str "$pid")
            printf "%-8s \033[32m%-10s\033[0m %-8s %s\n" "$t" "running" "$pid" "$up"
        else
            printf "%-8s \033[31m%-10s\033[0m\n" "$t" "stopped"
        fi
    done
    echo ""
}

# ── usage ──────────────────────────────────────────────────────────────────────

usage() {
    cat <<EOF
Usage: $(basename "$0") {start|stop|restart|status} [nf ...]

  start   [nf ...]   Start all 5GC NFs (or specific ones), in order
  stop    [nf ...]   Stop all 5GC NFs in reverse order (or specific ones)
  restart [nf ...]   Stop then start (all or specific NFs)
  status  [nf ...]   Show running status

NF order: ${NFS_5GC[*]} webui

Examples:
  $(basename "$0") start
  $(basename "$0") stop amf smf upf
  $(basename "$0") restart upf
  $(basename "$0") restart webui
  $(basename "$0") status
EOF
    exit 1
}

# ── main ───────────────────────────────────────────────────────────────────────

[[ $# -lt 1 ]] && usage
CMD=$1; shift
case $CMD in
    start)   do_start   "$@" ;;
    stop)    do_stop    "$@" ;;
    restart) do_restart "$@" ;;
    status)  do_status  "$@" ;;
    *)       usage ;;
esac
