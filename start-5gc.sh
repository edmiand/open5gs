#!/bin/bash
set -e

INSTALL_DIR="$HOME/open5gs/install"
BIN_DIR="$INSTALL_DIR/bin"
LOG_DIR="$INSTALL_DIR/var/log/open5gs"

mkdir -p "$LOG_DIR"

start_nf() {
    local name=$1
    local binary="$BIN_DIR/open5gs-${name}d"
    local logfile="$LOG_DIR/${name}.log"

    if pgrep -f "open5gs-${name}d" > /dev/null 2>&1; then
        echo "${name}: already running"
        return
    fi

    "$binary" -l info > "$logfile" 2>&1 &
    echo "${name}: started (pid $!)"
    sleep 0.5
}

stop_all() {
    echo "Stopping all open5gs NFs..."
    pkill -f "open5gs-" 2>/dev/null || true
    sleep 1
    echo "Done."
}

case "${1:-start}" in
    start)
        echo "Starting 5G Core NFs..."
        start_nf nrf
        sleep 1
        start_nf scp
        sleep 1
        start_nf amf
        start_nf smf
        start_nf upf
        start_nf ausf
        start_nf udm
        start_nf pcf
        start_nf nssf
        start_nf bsf
        start_nf udr
        echo "All NFs started."
        ;;
    stop)
        stop_all
        ;;
    *)
        echo "Usage: $0 {start|stop}"
        exit 1
        ;;
esac
