#!/usr/bin/env bash
# Set up the open5gs-camara gateway.
#
#   ./setup.sh                    venv (uv, Python 3.12) + deps + config.yaml with fresh secrets
#   ./setup.sh --dev              ... plus test deps
#   ./setup.sh --install-service  ... plus install/enable/start open5gs-camara.service (sudo)
#   ./setup.sh --specs            (re)download pinned CAMARA OpenAPI specs into specs/
#
# Fails loudly on any error.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
VENV="$HERE/.venv"
CONF="$HERE/config.yaml"
UNIT=open5gs-camara.service

die() { echo "setup.sh: ERROR: $*" >&2; exit 1; }
log() { echo "setup.sh: $*"; }

DEV=0; SERVICE=0; SPECS=0
for a in "$@"; do
    case $a in
        --dev) DEV=1 ;;
        --install-service) SERVICE=1 ;;
        --specs) SPECS=1 ;;
        -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
        *) die "unknown option: $a" ;;
    esac
done

# ── pinned CAMARA specs: <repo>@<tag>:<file> ──────────────────────────────────
SPEC_LIST=(
    "QoSProfiles@r1.2:qos-profiles.yaml"
    "QualityOnDemand@r4.3:quality-on-demand.yaml"
    "QualityOnDemand@r4.3:qos-provisioning.yaml"
    "SimSwap@r4.3:sim-swap.yaml"
    "SimSwap@r4.3:sim-swap-subscriptions.yaml"
    "DeviceSwap@r4.3:device-swap.yaml"
    "NumberVerification@r4.2:number-verification.yaml"
    "DeviceRoamingStatus@r2.3:device-roaming-status.yaml"
    "DeviceRoamingStatus@r2.3:device-roaming-status-subscriptions.yaml"
    "ConnectivityInsights@r4.3:connectivity-insights.yaml"
    "ConnectivityInsights@r4.3:connectivity-insights-subscriptions.yaml"
    "ApplicationProfiles@r2.3:application-profiles.yaml"
)

fetch_specs() {
    command -v curl >/dev/null || die "curl not found"
    for s in "${SPEC_LIST[@]}"; do
        local repo=${s%%@*} rest=${s#*@}; local tag=${rest%%:*} file=${rest#*:}
        local dir="$HERE/specs/$repo@$tag"; mkdir -p "$dir"
        curl -fsSL "https://raw.githubusercontent.com/camaraproject/$repo/$tag/code/API_definitions/$file" \
            -o "$dir/$file" || die "download failed: $repo@$tag/$file"
        log "spec $repo@$tag/$file"
    done
}

if (( SPECS )); then fetch_specs; exit 0; fi

# ── venv + deps ──────────────────────────────────────────────────────────────
command -v uv >/dev/null || die "uv not found (expected ~/.local/bin/uv, as used by open5gs-mcp)"
[[ -x $VENV/bin/python ]] || uv venv -q --python 3.12 "$VENV"
REQ="$HERE/requirements.txt"; (( DEV )) && REQ="$HERE/requirements-dev.txt"
uv pip install -q --python "$VENV/bin/python" -r "$REQ"
log "deps installed from $(basename "$REQ")"

# ── config.yaml with generated secrets ───────────────────────────────────────
if [[ ! -f $CONF ]]; then
    umask 077
    key=$("$VENV/bin/python" -c 'import secrets;print(secrets.token_urlsafe(48))')
    sec=$("$VENV/bin/python" -c 'import secrets;print(secrets.token_urlsafe(24))')
    # first CHANGE_ME = signing_key, second = demo-app client_secret
    awk -v k="$key" -v s="$sec" '/CHANGE_ME/ && !d1 {sub(/CHANGE_ME/,k); d1=1; print; next}
                                 /CHANGE_ME/ && !d2 {sub(/CHANGE_ME/,s); d2=1} {print}' \
        "$HERE/config.example.yaml" > "$CONF"
    grep -q CHANGE_ME "$CONF" && die "config.yaml still has CHANGE_ME placeholders"
    log "created $CONF (mode 600) — demo-app client_secret is in it"
else
    log "keeping existing $CONF"
fi
"$VENV/bin/python" -c "import sys; sys.path.insert(0, '$HERE'); from camara_gw.config import load_config; load_config('$CONF')" \
    || die "config.yaml failed validation"

# ── systemd unit ─────────────────────────────────────────────────────────────
if (( SERVICE )); then
    tmp=$(mktemp)
    sed -e "s|@USER@|$(id -un)|g" -e "s|@DIR@|$HERE|g" "$HERE/open5gs-camara.service.in" > "$tmp"
    sudo install -m 644 "$tmp" "/etc/systemd/system/$UNIT"; rm -f "$tmp"
    sudo systemctl daemon-reload
    sudo systemctl enable --now "$UNIT"
    sleep 2
    systemctl is-active --quiet "$UNIT" || { sudo journalctl -u "$UNIT" -n 30 --no-pager; die "$UNIT failed to start"; }
    log "$UNIT active"
fi
