#!/usr/bin/env bash
# Control script for Open5GS 5GC (prefix install at ~/open5gs/install/)

INSTALL_DIR="$(cd "$(dirname "$0")/install" && pwd)"
BIN="$INSTALL_DIR/bin"
LOGDIR="$INSTALL_DIR/var/log/open5gs"
RUNDIR="$INSTALL_DIR/var/run/open5gs"

# 5GC NFs in dependency order
NFS_5GC=(nrf scp amf smf upf ausf udm pcf nssf bsf udr)
# NFs that must run as root (need TUN/raw sockets)
NFS_ROOT=(upf)

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

do_start() {
    local -a targets=("${@:-${NFS_5GC[@]}}")
    mkdir -p "$RUNDIR"
    echo "Starting Open5GS 5GC..."
    local nf; for nf in "${targets[@]}"; do start_one "$nf"; done
    echo "Done."
}

do_stop() {
    local -a targets
    if [[ $# -gt 0 ]]; then
        targets=("$@")
    else
        # reverse order for teardown
        targets=()
        local i; for (( i=${#NFS_5GC[@]}-1; i>=0; i-- )); do
            targets+=("${NFS_5GC[$i]}")
        done
    fi
    echo "Stopping Open5GS 5GC..."
    local nf; for nf in "${targets[@]}"; do stop_one "$nf"; done
    echo "Done."
}

do_restart() {
    local -a targets=("${@:-${NFS_5GC[@]}}")
    echo "Restarting Open5GS 5GC..."
    local -a rev=()
    local i; for (( i=${#targets[@]}-1; i>=0; i-- )); do rev+=("${targets[$i]}"); done
    local nf
    for nf in "${rev[@]}"; do stop_one "$nf"; done
    sleep 1
    for nf in "${targets[@]}"; do start_one "$nf"; done
    echo "Done."
}

do_status() {
    local -a targets=("${@:-${NFS_5GC[@]}}")
    printf "\n%-8s %-10s %-8s %s\n" "NF" "STATUS" "PID" "UPTIME"
    printf "%-8s %-10s %-8s %s\n"   "--------" "----------" "--------" "-------"
    local nf
    for nf in "${targets[@]}"; do
        local pid; pid=$(get_pid "$nf")
        if [[ -n $pid ]] && [[ -d /proc/$pid ]]; then
            local up; up=$(proc_uptime_str "$pid")
            printf "%-8s \033[32m%-10s\033[0m %-8s %s\n" "$nf" "running" "$pid" "$up"
        else
            printf "%-8s \033[31m%-10s\033[0m\n" "$nf" "stopped"
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

NF order: ${NFS_5GC[*]}

Examples:
  $(basename "$0") start
  $(basename "$0") stop amf smf upf
  $(basename "$0") restart upf
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
