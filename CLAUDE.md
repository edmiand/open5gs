# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Open5GS is a C implementation of 5G Core and 4G EPC network functions (AGPL-3.0-or-later),
built with Meson/Ninja. This repo (`edmiand/open5gs`) is a fork used to run a personal 5G Core
lab; lab-specific operating instructions are at the bottom of this file.

## Build

```bash
meson setup build --prefix=$(pwd)/install   # first time only
ninja -C build
ninja -C build install
```

- `c_std=gnu89`, `warning_level=1`, requires `meson >= 0.43.0` (see `meson.build`).
- Re-running `meson setup build` after a `git pull` isn't needed — `ninja -C build` regenerates
  build files automatically when `meson.build` files change.
- `meson_options.txt` exposes `-Dfuzzing=true` and `-Dlib_fuzzing_engine=<path>` for oss-fuzz builds.

## Test

```bash
meson test -C build                    # run all suites
meson test -C build registration -v    # run a single suite, verbose
meson test -C build --list             # list all suites
```

Suites (from `tests/meson.build`), grouped by `meson test` suite prefix:
- `open5gs:5gc` — `registration`, `vonr`, `slice`, `transfer`, `transfer-error`
- `open5gs:epc` — `attach`, `volte`, `csfb`, `non3gpp`
- `open5gs:app` — `sctp`, `310014`, `handover`
- `open5gs:unit` — `core`, `crypt`, `unit`

Each suite is one ABTS-based test binary under `tests/<suite>/` (built from several `*-test.c`
files registered in that directory's `abts-main.c`); `meson test` selects a whole suite/binary,
not individual test cases inside it. The `5gc`/`epc` integration suites spin up real NF daemons
in-process and drive them through NAS/NGAP/S1AP scenarios, so they need the `ogstun` TUN device
and a reachable MongoDB — see `.github/workflows/*.yml` for the exact CI setup used before tests
will pass.

## Lint / static analysis

```bash
ninja -C build analyze-cppcheck     # requires cppcheck
ninja -C build analyze-clang-tidy   # requires clang-tidy or run-clang-tidy
```

Rules are in `.clang-tidy`; both targets only register if the tool is found on `PATH` at
`meson setup` time.

## Architecture

`lib/` — shared libraries linked into every NF daemon:
- `lib/core` — base runtime (list, hash, fsm, timer, log, memory pools); also bundles the ABTS
  test framework (`lib/core/abts.c`) used by everything under `tests/`.
- `lib/app` — config loading (`ogs-config.c` parses `install/etc/open5gs/*.yaml`) and the
  init/context lifecycle every daemon shares.
- `lib/sbi` — 5G SBI (HTTP/2 service-based interface) client/server; how 5GC NFs discover each
  other via NRF and call each other's APIs.
- `lib/nas`, `lib/ngap`, `lib/s1ap`, `lib/diameter`, `lib/pfcp`, `lib/gtp` — protocol encode/decode
  and message handling for 5G NAS/NGAP, 4G S1AP/Diameter, PFCP (SMF↔UPF), and GTP tunneling.
- `lib/asn1c` — generated ASN.1 codecs backing NGAP/S1AP.
- `lib/dbi` — MongoDB-backed subscriber DB access (used by UDR).
- `lib/tun`, `lib/sctp`, `lib/crypt`, `lib/metrics`, `lib/ipfw` — TUN device management, SCTP
  transport, crypto, Prometheus metrics, IP packet filtering.

`src/<nf>/` — one directory per NF daemon, each built into its own `open5gs-<nf>d` executable
(`src/main.c` is the shared entrypoint linked into all of them; it parses common CLI flags,
calls `ogs_app_initialize()`, then the NF's own `app_initialize()`). NFs present:
- 5GC: `amf`, `smf`, `upf`, `ausf`, `udm`, `udr`, `pcf`, `nssf`, `bsf`, `nrf`, `scp`, `sepp`
- 4G EPC: `mme`, `sgwc`, `sgwu`, `hss`, `pcrf`

Each NF directory follows the same shape:
- `app.c` / `init.c` — daemon entrypoint, wires into `lib/app`.
- `context.c` / `context.h` — the NF's in-memory state.
- `<nf>-sm.c` (and sub-FSMs like `gmm-sm.c` in `amf`) — the main FSM driving the daemon's event loop.
- `n<peer>-build.c` / `n<peer>-handler.c` — build outgoing / handle incoming SBI messages to/from
  a specific peer NF (e.g. `src/amf/nausf-build.c`, `src/amf/nudm-handler.c`).
- `event.c`, `timer.c` — NF-specific event definitions and timers.

`tests/<suite>/` — integration suites as described above; `tests/unit/` covers protocol message
encode/decode without starting full NF daemons; `tests/common/` and `tests/app/` hold shared
test-harness helpers (in-process NF startup, fake UE/gNB drivers) used by the other suites.

---

## Lab Deployment — edmiand fork, arm64 Ubuntu 22.04

Full step-by-step instructions for deploying this fork on a new arm64 Ubuntu 22.04 VM
are in [README.md](README.md#lab-deployment--edmiand-fork-arm64-ubuntu-2204).

### Target configuration

| Parameter | Value |
|-----------|-------|
| Fork | `https://github.com/edmiand/open5gs` |
| Install prefix | `~/open5gs/install` |
| PLMN | MCC `999`, MNC `70` |
| TAC | `1` |
| NSSAI SST | `1` |
| AMF NGAP / UPF GTP-U | VM's primary non-loopback IP (auto-detect) |
| TUN | `ogstun` — `10.45.0.1/16`, `2001:db8:cafe::1/48` |

### Key constraints

- All config edits non-interactive; only touch `install/etc/open5gs/*.yaml`.
- `sudo` only where required (packages, sysctl, iptables, systemd, tun setup).
- Fail loudly on any error — never silently continue.
- 5G Core only — skip 4G EPC NFs (MME, SGW-C/U, HSS, PCRF).
- WebUI systemd unit: enable but do **not** start (PORT=9999, HOSTNAME=0.0.0.0).

### Operations

See `open5gs-ctl.sh` for start/stop/status of NF processes.

NF startup order: NRF → SCP → AMF → SMF → UPF → AUSF → UDM → PCF → NSSF → BSF → UDR

Logs: `~/open5gs/install/var/log/open5gs/<nf>.log`
