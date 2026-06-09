#!/bin/bash

NFS="nrf scp amf smf upf ausf udm pcf nssf bsf udr"

for nf in $NFS; do
    if pgrep -f "open5gs-${nf}d" > /dev/null 2>&1; then
        pid=$(pgrep -f "open5gs-${nf}d" | head -1)
        echo "${nf}: running (pid ${pid})"
    else
        echo "${nf}: stopped"
    fi
done
