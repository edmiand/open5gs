#!/usr/bin/env bash
# open5gs-upgrade.sh — safe, reversible upgrade of this Open5GS 5GC checkout.
#
# Self-contained: no dependency outside this repo. Operates on this checkout
# via git/meson/ninja and open5gs-ctl.sh; never hand-edits any file, and
# never touches install/etc/open5gs/*.yaml (config drift is surfaced, not
# applied — see step 7 below).
#
# Flow: fetch upstream -> show what's new -> (confirm) -> snapshot rollback
# point -> merge -> rebuild -> diff deployed configs against the new
# defaults -> stop NFs -> install -> start NFs -> health-check each ->
# roll back automatically if any NF fails to come up healthy.
#
# Usage:
#   open5gs-upgrade.sh --dry-run           Show what's new, stop there
#   open5gs-upgrade.sh                     Full upgrade, asks to confirm
#   open5gs-upgrade.sh --yes               Full upgrade, no prompts
#   open5gs-upgrade.sh --rollback          Revert to the last recorded good commit
#
set -uo pipefail

main() {
    local SCRIPT_DIR; SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    # OPEN5GS_DIR and SHA_FILE are deliberately global (no `local`): on_interrupt
    # below needs to read them if a signal cuts this script off mid-operation.
    OPEN5GS_DIR="${OPEN5GS_DIR:-$SCRIPT_DIR}"
    # State/logs live under install/, which is wholly gitignored — keeps
    # this checkout clean of scratch files that don't belong in the repo.
    local STATE_DIR="$OPEN5GS_DIR/install/.upgrade-state"
    local LOG_DIR="$OPEN5GS_DIR/install/var/log/open5gs-upgrade"
    SHA_FILE="$STATE_DIR/last-good.sha"
    local RUN_LOG="$LOG_DIR/upgrade-$(date -u +%Y%m%dT%H%M%SZ).log"
    mkdir -p "$STATE_DIR" "$LOG_DIR"

    local DRY_RUN=0 ASSUME_YES=0 DO_ROLLBACK=0
    while [[ $# -gt 0 ]]; do
        case $1 in
            --dry-run)  DRY_RUN=1; shift ;;
            --yes|-y)   ASSUME_YES=1; shift ;;
            --rollback) DO_ROLLBACK=1; shift ;;
            -h|--help)
                sed -n '2,19p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
                exit 0 ;;
            *) echo "Unknown option: $1" >&2; exit 2 ;;
        esac
    done

    # Tee everything to a run log, but keep it readable on the terminal too.
    exec > >(tee -a "$RUN_LOG") 2>&1

    echo "== open5gs-upgrade $(date -u +%FT%TZ) =="
    echo "Target: $OPEN5GS_DIR"
    echo

    [[ -d "$OPEN5GS_DIR/.git" ]] || die "Not a git checkout: $OPEN5GS_DIR"

    local NFS_ORDER=(nrf scp amf smf upf ausf udm pcf nssf bsf udr)
    local NFS_ROOT=(upf)
    # Also global — same reason as OPEN5GS_DIR/SHA_FILE above.
    CTL="$OPEN5GS_DIR/open5gs-ctl.sh"
    [[ -x "$CTL" ]] || die "Control script not found or not executable: $CTL"

    # If this script is killed mid-operation (dropped SSH session, closed
    # terminal, manual kill), don't let it fail silently into an inconsistent
    # state. This matters especially here because NFs are daemonized (-D):
    # they double-fork and detach into their own session, so a signal sent to
    # this script's process group does NOT reach them — only non-daemonized
    # children (e.g. webui, run as a plain background job) die with it. That
    # split is exactly what makes an interrupted stop/rollback confusing: some
    # NFs stop, most don't, and the terminal just shows "Terminated" with no
    # indication of which is which.
    trap on_interrupt INT TERM

    if (( DO_ROLLBACK )); then
        do_manual_rollback "$OPEN5GS_DIR" "$SHA_FILE" "$CTL" "${NFS_ROOT[*]}" "${NFS_ORDER[@]}"
        exit $?
    fi

    # ── 1. refuse on dirty tree ─────────────────────────────────────────────
    local dirty
    dirty=$(git -C "$OPEN5GS_DIR" status --porcelain --untracked-files=no)
    if [[ -n $dirty ]]; then
        echo "ERROR: $OPEN5GS_DIR has uncommitted changes to tracked files:" >&2
        echo "$dirty" >&2
        echo "Commit, stash, or discard them before upgrading." >&2
        exit 1
    fi

    if ! git -C "$OPEN5GS_DIR" remote get-url upstream >/dev/null 2>&1; then
        die "No 'upstream' remote configured in $OPEN5GS_DIR (expected open5gs/open5gs). Run: git remote add upstream https://github.com/open5gs/open5gs.git"
    fi

    # ── 2. fetch + show what's new ──────────────────────────────────────────
    echo "Fetching upstream..."
    git -C "$OPEN5GS_DIR" fetch upstream || die "git fetch upstream failed"

    local old_sha new_sha
    old_sha=$(git -C "$OPEN5GS_DIR" rev-parse HEAD)
    new_sha=$(git -C "$OPEN5GS_DIR" rev-parse upstream/main)

    if git -C "$OPEN5GS_DIR" merge-base --is-ancestor upstream/main HEAD; then
        echo "Already up to date with upstream/main (upstream/main is an ancestor of HEAD, $old_sha)."
        exit 0
    fi

    echo
    echo "New commits (HEAD..upstream/main):"
    git -C "$OPEN5GS_DIR" log --oneline "HEAD..upstream/main" | sed 's/^/  /'
    echo
    echo "Files changed:"
    git -C "$OPEN5GS_DIR" diff --stat "HEAD..upstream/main" | sed 's/^/  /'
    echo

    local config_templates_touched
    config_templates_touched=$(git -C "$OPEN5GS_DIR" diff --name-only "HEAD..upstream/main" -- 'configs/open5gs/*.yaml.in')
    if [[ -n $config_templates_touched ]]; then
        echo "NOTE: default config templates changed upstream — will diff against your deployed configs after rebuild:"
        echo "$config_templates_touched" | sed 's/^/  /'
        echo
    fi

    local webui_deps_touched
    webui_deps_touched=$(git -C "$OPEN5GS_DIR" diff --name-only "HEAD..upstream/main" -- webui/package.json webui/package-lock.json)

    if (( DRY_RUN )); then
        echo "Dry run — stopping here. No changes made."
        exit 0
    fi

    # ── 3. confirm before anything destructive ──────────────────────────────
    if (( ! ASSUME_YES )); then
        confirm "Proceed with merge, rebuild, and rolling restart of all NFs?" || { echo "Aborted."; exit 1; }
    fi

    # ── 4. record rollback point ────────────────────────────────────────────
    echo "$old_sha" > "$SHA_FILE"
    echo "Recorded rollback point: $old_sha -> $SHA_FILE"

    # ── 5. merge ─────────────────────────────────────────────────────────────
    echo "Merging upstream/main..."
    if ! git -C "$OPEN5GS_DIR" merge --no-edit upstream/main; then
        echo "ERROR: merge failed (conflicts). Aborting merge, no NFs touched." >&2
        git -C "$OPEN5GS_DIR" merge --abort
        exit 1
    fi

    # ── 6. rebuild ───────────────────────────────────────────────────────────
    echo "Rebuilding (ninja -C build)..."
    if ! ninja -C "$OPEN5GS_DIR/build"; then
        echo "ERROR: build failed. Source tree is now at the new commit but NOT installed/restarted — old binaries and NFs are untouched and still running." >&2
        echo "Fix the build or run: $0 --rollback" >&2
        exit 1
    fi

    if [[ -n $webui_deps_touched ]]; then
        echo "webui dependencies changed upstream — running npm ci..."
        (cd "$OPEN5GS_DIR/webui" && npm ci) || echo "WARNING: npm ci failed — webui may need manual attention."
    fi

    # ── 7. surface config drift (never auto-apply) ──────────────────────────
    echo
    echo "Checking deployed configs against the new defaults..."
    local drift=0
    local nf
    for nf in "${NFS_ORDER[@]}"; do
        local built="$OPEN5GS_DIR/build/configs/open5gs/${nf}.yaml"
        local deployed="$OPEN5GS_DIR/install/etc/open5gs/${nf}.yaml"
        [[ -f $built && -f $deployed ]] || continue
        if ! yaml_key_diff "$built" "$deployed"; then
            drift=1
        fi
    done
    echo

    if (( drift )); then
        echo "New or removed config keys were found (printed above). They are NOT applied automatically —"
        echo "review install/etc/open5gs/*.yaml by hand. An affected NF may fail to start or behave"
        echo "incorrectly without them."
        if (( ! ASSUME_YES )); then
            local ack
            read -r -p "Type UPGRADE-ANYWAY to proceed despite the config drift above, or anything else to abort: " ack
            [[ $ack == "UPGRADE-ANYWAY" ]] || { echo "Aborted before touching any running NF. Source tree is already merged+built at $new_sha; roll back with --rollback if you want the old commit back too."; exit 1; }
        else
            echo "--yes given: proceeding despite config drift (unattended mode)."
        fi
    fi

    # ── 8. stop -> install -> start ─────────────────────────────────────────
    capture_log_baselines "$OPEN5GS_DIR" "${NFS_ROOT[*]}" "${NFS_ORDER[@]}"
    echo "Stopping NFs (reverse dependency order)..."
    "$CTL" stop

    echo "Installing (ninja -C build install)..."
    if ! ninja -C "$OPEN5GS_DIR/build" install; then
        echo "ERROR: install failed after NFs were stopped. Attempting rollback..." >&2
        rollback_to "$OPEN5GS_DIR" "$old_sha" "$CTL" "${NFS_ROOT[*]}" "${NFS_ORDER[@]}"
        exit 1
    fi

    echo "Starting NFs (dependency order)..."
    "$CTL" start

    # ── 9. health-check ──────────────────────────────────────────────────────
    echo "Waiting for NFs to settle..."
    sleep 3
    if health_check_all "$OPEN5GS_DIR" "${NFS_ROOT[*]}" "${NFS_ORDER[@]}"; then
        echo
        echo "== SUCCESS == Open5GS upgraded $old_sha -> $new_sha, all NFs healthy."
        echo "Rollback point still recorded at $SHA_FILE if you need it later."
        exit 0
    fi

    echo
    echo "== HEALTH CHECK FAILED == rolling back to $old_sha automatically..." >&2
    if rollback_to "$OPEN5GS_DIR" "$old_sha" "$CTL" "${NFS_ROOT[*]}" "${NFS_ORDER[@]}"; then
        if health_check_all "$OPEN5GS_DIR" "${NFS_ROOT[*]}" "${NFS_ORDER[@]}"; then
            echo "== ROLLED BACK OK == core is back on $old_sha and healthy. Upgrade to $new_sha did NOT stick." >&2
            exit 1
        fi
    fi

    echo "== CRITICAL == rollback itself did not come up healthy. Manual intervention required." >&2
    echo "  Old (known-good) commit: $old_sha" >&2
    echo "  Attempted new commit:    $new_sha" >&2
    echo "  Check: $CTL status ; tail -f $OPEN5GS_DIR/install/var/log/open5gs/*.log" >&2
    exit 2
}

die() { echo "ERROR: $*" >&2; exit 1; }

# on_interrupt — trapped on INT/TERM (see trap install site in main). Prints
# what actually happened instead of leaving a bare "Terminated" and a stale
# "rolling back automatically..." banner that never finished. OPEN5GS_DIR,
# SHA_FILE, and CTL are set as globals in main() specifically so this handler
# can read them regardless of where main() was interrupted.
on_interrupt() {
    trap - INT TERM   # don't re-enter on a second signal while we print
    echo >&2
    echo "== INTERRUPTED == open5gs-upgrade.sh was killed mid-operation." >&2
    echo "Any banner printed above (e.g. \"rolling back automatically...\") may not have" >&2
    echo "finished — treat it as unknown, not as handled. Actual state:" >&2
    echo "  git HEAD now:            $(git -C "$OPEN5GS_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)" >&2
    if [[ -f ${SHA_FILE:-} ]]; then
        echo "  last known-good commit:  $(cat "$SHA_FILE")" >&2
    else
        echo "  last known-good commit:  none recorded" >&2
    fi
    echo "  NFs may be a mix of stopped and still-running: daemonized NFs (-D) detach" >&2
    echo "  from this terminal's session, so this signal did not reach them — only" >&2
    echo "  non-daemonized processes (e.g. webui) were killed along with this script." >&2
    echo >&2
    echo "Recovery:" >&2
    echo "  1. ${CTL:-./open5gs-ctl.sh} status   # see what is actually running" >&2
    echo "  2. $0 --rollback           # idempotent — safe to re-run; forces git," >&2
    echo "                                          # binaries, and every NF back onto" >&2
    echo "                                          # the last known-good commit" >&2
    exit 130
}

confirm() {
    local prompt=$1 ans
    read -r -p "$prompt [y/N] " ans
    [[ $ans == y || $ans == Y || $ans == yes ]]
}

# yaml_key_diff <new_template.yaml> <deployed.yaml> — structural key-path
# diff (not a raw line diff, so address/log-level customizations don't
# drown out keys that actually matter). Exit: 0 = no structural diff,
# 1 = new and/or removed keys found (printed, never applied).
yaml_key_diff() {
    python3 - "$1" "$2" <<'PYEOF'
import sys, yaml

def flatten(obj, prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            yield from flatten(v, path)
    elif isinstance(obj, list):
        if not obj:
            yield (prefix, obj)
        for i, v in enumerate(obj):
            yield from flatten(v, f"{prefix}[{i}]")
    else:
        yield (prefix, obj)

def load(path):
    with open(path) as f:
        data = yaml.safe_load(f)
    return data or {}

new_path, deployed_path = sys.argv[1], sys.argv[2]
try:
    new_cfg = dict(flatten(load(new_path)))
    old_cfg = dict(flatten(load(deployed_path)))
except Exception as exc:
    print(f"ERROR: could not parse YAML - {exc}", file=sys.stderr)
    sys.exit(2)

new_keys = sorted(set(new_cfg) - set(old_cfg))
removed_keys = sorted(set(old_cfg) - set(new_cfg))
changed_keys = sorted(k for k in (set(new_cfg) & set(old_cfg)) if new_cfg[k] != old_cfg[k])

def collapse_list_paths(keys):
    seen = {}
    for k in keys:
        root = k.split("[", 1)[0]
        seen[root] = seen.get(root, 0) + 1
    return seen

if not new_keys and not removed_keys:
    print(f"  {deployed_path}: no structural key differences ({len(changed_keys)} value-only change(s))")
    sys.exit(0)

print(f"  {deployed_path}")
if new_keys:
    print("    NEW keys upstream (not in deployed config):")
    for root, count in collapse_list_paths(new_keys).items():
        suffix = f"  (x{count} list entries)" if count > 1 else ""
        print(f"      + {root}{suffix}")
if removed_keys:
    print("    Keys in deployed config absent from the fresh template")
    print("    (often a commented-out-by-default key you've deliberately turned on -")
    print("     e.g. logger.level - but check for deprecated/renamed keys too):")
    for root, count in collapse_list_paths(removed_keys).items():
        suffix = f"  (x{count} list entries)" if count > 1 else ""
        print(f"      - {root}{suffix}")
if changed_keys:
    print(f"    {len(changed_keys)} value-only difference(s) (expected - your customizations)")

sys.exit(1)
PYEOF
}

# metrics_url_for <open5gs_dir> <nf> — real bound metrics address:port from
# the deployed YAML, falling back to Open5GS's documented per-NF defaults.
metrics_url_for() {
    local open5gs_dir=$1 nf=$2
    python3 - "$open5gs_dir/install/etc/open5gs/${nf}.yaml" "$nf" <<'PYEOF'
import sys, yaml
path, nf = sys.argv[1], sys.argv[2]
defaults = {
    "amf": "127.0.0.5", "smf": "127.0.0.4", "upf": "127.0.0.7", "ausf": "127.0.0.11",
    "udm": "127.0.0.12", "udr": "127.0.0.20", "pcf": "127.0.0.13", "nssf": "127.0.0.14",
    "bsf": "127.0.0.15", "nrf": "127.0.0.10", "scp": "127.0.0.200",
}
try:
    with open(path) as f:
        cfg = yaml.safe_load(f)
    srv = cfg[nf]["metrics"]["server"][0]
    print(f"http://{srv['address']}:{srv['port']}")
except Exception:
    print(f"http://{defaults.get(nf, '127.0.0.1')}:9090")
PYEOF
}

# capture_log_baselines <open5gs_dir> <root_nfs_space_separated> <nf...> —
# record each NF's current log line count before a stop/start cycle. NF logs
# are never truncated across restarts, so without this, health_check_all's
# "recent errors" check would flag stale ERROR/FATAL lines left over from a
# prior day's session as if they were caused by this restart. root_nfs (e.g.
# upf) write logs as root, so those need sudo to read, same as health_check_all.
capture_log_baselines() {
    local open5gs_dir=$1 root_nfs=$2; shift 2
    local baseline_dir="$open5gs_dir/install/.upgrade-state/log-baseline"
    mkdir -p "$baseline_dir"
    local nf log lines
    for nf in "$@"; do
        log="$open5gs_dir/install/var/log/open5gs/${nf}.log"
        if [[ " $root_nfs " == *" $nf "* ]]; then
            lines=$(sudo cat "$log" 2>/dev/null | wc -l)
        else
            lines=$(wc -l < "$log" 2>/dev/null)
        fi
        echo "${lines:-0}" > "$baseline_dir/${nf}.lines"
    done
}

# health_check_all <open5gs_dir> <root_nfs_space_separated> <nf...>
# Real health, not just "process exists": pid alive; metrics endpoint
# responding (only checked for NFs that actually have a metrics: block
# configured — most 5GC NFs in this lab don't, so skipping the rest avoids
# a guaranteed-unreachable false positive); and no new FATAL/ERROR log
# lines since the restart, per capture_log_baselines above (not just "last
# 100 lines", which picks up stale errors from long-lived, never-truncated
# logs regardless of how old they are).
health_check_all() {
    local open5gs_dir=$1 root_nfs=$2; shift 2
    local nf all_ok=1 filtered_total=0
    local baseline_dir="$open5gs_dir/install/.upgrade-state/log-baseline"
    # Each entry: a startup-time ERROR/FATAL pattern confirmed present across
    # multiple *unrelated* restarts (not introduced by any upgrade), so
    # counting it would fail health_check_all on noise instead of regressions.
    #   - "STREAM has already been removed" (src/scp/sbi-path.c:858, upstream
    #     6cb518539b from 2024-06-12): a stream-cleanup race when several NFs
    #     register with SCP in the same burst at startup. Seen identically in
    #     restarts on 2026-08-07, 2026-08-08, and 2026-08-11 — including the
    #     2026-08-11 run that wasn't near any change to lib/sbi or src/scp.
    #   - "NF instance FSM has been finalized" (src/scp/scp-sm.c:200): logged
    #     deliberately, per the comment right above that call site, when an
    #     SBI response (e.g. an NRF-notify callback) arrives for an NF
    #     instance whose FSM was already torn down by event_termination() —
    #     the code explicitly logs-and-drops instead of dispatching into a
    #     dead FSM, so this is the intended safe path, not a crash. Seen on
    #     the 2026-08-14 restart, which touched amf/mme/smf only, nowhere
    #     near src/scp.
    local BENIGN_STARTUP_ERR_PATTERNS="STREAM has already been removed|NF instance FSM has been finalized"
    printf "\n%-8s %-8s %-12s %-10s\n" "NF" "PID" "METRICS" "RECENT-ERR"
    printf "%-8s %-8s %-12s %-10s\n" "--------" "--------" "------------" "----------"
    for nf in "$@"; do
        local pid="" ok=1
        pid=$(pgrep "open5gs-${nf}d" 2>/dev/null | head -1)
        if [[ -z $pid ]]; then ok=0; fi

        local metrics_status="n/a"
        if [[ -n $pid ]] && grep -q "metrics:" "$open5gs_dir/install/etc/open5gs/${nf}.yaml" 2>/dev/null; then
            local url; url=$(metrics_url_for "$open5gs_dir" "$nf")
            if curl -sf --max-time 2 "${url}/metrics" >/dev/null 2>&1; then
                metrics_status="ok"
            else
                metrics_status="unreachable"
                ok=0
            fi
        fi

        local log="$open5gs_dir/install/var/log/open5gs/${nf}.log"
        local baseline=""
        [[ -f "$baseline_dir/${nf}.lines" ]] && baseline=$(cat "$baseline_dir/${nf}.lines")
        [[ $baseline =~ ^[0-9]+$ ]] || baseline=0
        local total_lines=""
        if [[ " $root_nfs " == *" $nf "* ]]; then
            total_lines=$(sudo cat "$log" 2>/dev/null | wc -l)
        else
            total_lines=$(wc -l < "$log" 2>/dev/null)
        fi
        [[ $total_lines =~ ^[0-9]+$ ]] || total_lines=0
        local new_lines=$(( total_lines - baseline ))
        (( new_lines < 0 )) && new_lines=0

        local err_count=0
        if (( new_lines > 0 )); then
            local raw_errlines
            if [[ " $root_nfs " == *" $nf "* ]]; then
                raw_errlines=$(sudo tail -n "$new_lines" "$log" 2>/dev/null | grep -Eai "FATAL|ERROR" || true)
            else
                raw_errlines=$(tail -n "$new_lines" "$log" 2>/dev/null | grep -Eai "FATAL|ERROR" || true)
            fi
            if [[ -n $raw_errlines ]]; then
                local raw_count kept_errlines kept_count
                raw_count=$(wc -l <<<"$raw_errlines")
                # Known-benign noise, not a regression: excluded so a routine
                # restart doesn't trigger a false-positive automatic rollback.
                # See BENIGN_STARTUP_ERR_PATTERNS above for why each entry is here.
                kept_errlines=$(grep -Eav "$BENIGN_STARTUP_ERR_PATTERNS" <<<"$raw_errlines" || true)
                if [[ -n $kept_errlines ]]; then
                    kept_count=$(wc -l <<<"$kept_errlines")
                else
                    kept_count=0
                fi
                err_count=$kept_count
                (( filtered_total += raw_count - kept_count ))
            fi
        fi
        (( err_count > 0 )) && ok=0

        printf "%-8s %-8s %-12s %-10s\n" "$nf" "${pid:-none}" "$metrics_status" "${err_count} since restart"
        (( ok )) || all_ok=0
    done
    (( filtered_total > 0 )) && echo "(ignored $filtered_total known-benign error line(s) — see BENIGN_STARTUP_ERR_PATTERNS in this script)"
    echo
    (( all_ok ))
}

# rollback_to <open5gs_dir> <target_sha> <ctl_script> <root_nfs_space_separated> <nf...>
rollback_to() {
    local open5gs_dir=$1 target_sha=$2 ctl=$3 root_nfs=$4; shift 4
    capture_log_baselines "$open5gs_dir" "$root_nfs" "$@"
    echo "Stopping NFs..."
    "$ctl" stop
    echo "Checking out $target_sha..."
    git -C "$open5gs_dir" checkout --detach "$target_sha" || return 1
    echo "Rebuilding old commit..."
    ninja -C "$open5gs_dir/build" || return 1
    ninja -C "$open5gs_dir/build" install || return 1
    # Return the branch pointer too, so `git status` doesn't strand the repo
    # in detached HEAD — old_sha is always an ancestor of the branch tip we
    # merged from, so this is a plain fast-forward-safe reset.
    git -C "$open5gs_dir" checkout main 2>/dev/null || true
    git -C "$open5gs_dir" reset --hard "$target_sha"
    echo "Starting NFs..."
    "$ctl" start
    sleep 3
}

do_manual_rollback() {
    local open5gs_dir=$1 sha_file=$2 ctl=$3 root_nfs=$4; shift 4
    [[ -f $sha_file ]] || die "No recorded rollback point at $sha_file — nothing to roll back to."
    local target_sha; target_sha=$(cat "$sha_file")
    echo "Rolling back $open5gs_dir to $target_sha..."
    rollback_to "$open5gs_dir" "$target_sha" "$ctl" "$root_nfs" "$@" || die "Rollback failed."
    if health_check_all "$open5gs_dir" "$root_nfs" "$@"; then
        echo "Rollback OK — core is on $target_sha and healthy."
        return 0
    fi
    echo "Rollback completed but health check still failing — investigate manually." >&2
    return 1
}

main "$@"
