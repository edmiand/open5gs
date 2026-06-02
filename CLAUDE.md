# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build

```bash
# Configure (first time only)
meson build --prefix=`pwd`/install

# Build — use -j2 on constrained VMs (ARM64/low-RAM) to avoid OOM
ninja -C build -j2

# Install to ./install/
ninja -C build install
```

The build system is **meson + ninja**. `build/` holds all build artifacts; `install/` holds the installed tree (`bin/`, `lib/`, `etc/`). Both are gitignored.

## Testing

### Prerequisites for integration tests
MongoDB must be running and TUN devices must exist:
```bash
# TUN devices are not persistent across reboots
sudo ip tuntap add name ogstun2 mode tun
sudo ip tuntap add name ogstun3 mode tun
sudo ./misc/netconf.sh
sudo ip link set ogstun2 up && sudo ip link set ogstun3 up
```

### Run tests

```bash
# Unit tests (no NFs spawned, fast)
./build/tests/core/core
./build/tests/crypt/crypt
./build/tests/unit/unit

# Integration tests (spawn real NF child processes)
./build/tests/registration/registration    # 5G NAS registration scenarios
./build/tests/handover/handover
./build/tests/attach/attach                # 4G EPC attach

# By suite via meson
meson test -C build --suite open5gs:unit   # unit tests only
meson test -C build --suite open5gs:5gc    # 5G integration tests
meson test -C build --suite open5gs:epc    # 4G EPC integration tests

# Run specific test cases within a suite (positional args filter by case name)
./build/tests/registration/registration simple-test guti-test
```

### Integration test pitfall
Integration tests spawn NF daemons as child processes. If a test crashes, those processes stay alive and hold their ports. The next run will fail with `Address already in use`. Fix:
```bash
pkill -f "open5gs-"
```

## Code Architecture

### Repository layout

- `src/` — one subdirectory per NF, each produces one binary (`open5gs-<nf>d`)
- `lib/` — shared protocol and infrastructure libraries (linked by NFs)
- `tests/` — integration test suites; each spawns NFs against `build/configs/sample.yaml`
- `configs/` — YAML config templates (copied into `build/configs/` at configure time)
- `subprojects/` — vendored: `freeDiameter` (Diameter stack), `prometheus-client-c`
- `misc/` — helper scripts: `netconf.sh` (TUN), `db_init.sh` (MongoDB seed), TLS cert gen

### Every NF has the same internal structure

```
src/<nf>/
  app.c          — NF entry: calls <nf>_initialize() / <nf>_terminate()
  init.c         — initialize(): starts FSM thread, registers with NRF, opens sockets
  context.c/h    — NF-global state (singleton context) + UE/session context structs
  event.c/h      — event type enum + event struct definition
  timer.c/h      — timer IDs and timer callback dispatch
  <nf>-sm.c      — top-level FSM (amf_state_operational, etc.)
  gmm-sm.c       — (AMF only) per-UE GMM state machine
  n<peer>-build.c    — builds outbound SBI HTTP requests to <peer>
  n<peer>-handler.c  — handles inbound SBI responses/notifications from <peer>
  <proto>-handler.c  — handles inbound NGAP / NAS / PFCP / GTP messages
  <proto>-build.c    — builds outbound NGAP / NAS / PFCP / GTP messages
  sbi-path.c         — NRF discovery + dispatches all outbound SBI calls
```

### Event-driven / FSM execution model

All NFs are single-threaded event loops. External messages (NGAP from gNB, NAS from UE, SBI responses from peer NFs) are decoded, wrapped in `<nf>_event_t`, and posted to an `ogs_queue_t`. The FSM thread dequeues events and dispatches them to the current state handler. State transitions use `OGS_FSM_TRAN(s, &next_state)`.

### SBI (Service-Based Interface) pattern

All 5G inter-NF communication uses HTTP/2 REST via `lib/sbi/`. The pattern for any outbound call:

1. `n<peer>-build.c` — `amf_n<peer>_<service>_build_<operation>()` constructs the `ogs_sbi_request_t`
2. `sbi-path.c` — `amf_ue_sbi_discover_and_send()` resolves the target NF instance via NRF, wraps in a transaction (`ogs_sbi_xact_t`), and sends
3. `n<peer>-handler.c` — `amf_n<peer>_<service>_handle_<operation>()` processes the response

NRF discovery is transparent — `ogs_sbi_discover_and_send()` in `lib/sbi/` handles it. Direct SCP routing is configured via `sbi.client.scp` in the NF's YAML.

### Key shared libraries

| Library | What it provides |
|---------|-----------------|
| `lib/core` | Foundation: memory (`ogs_pool_*`), logging (`ogs_log_*`), FSM (`ogs_fsm_*`), sockets, timers, queues, hash tables |
| `lib/sbi` | HTTP/2 client/server, NRF discovery, SBI message codec, openapi stubs |
| `lib/ngap` | NGAP ASN.1 codec (gNB↔AMF) |
| `lib/nas` | NAS 5GS/EPS codec (UE↔AMF/MME) |
| `lib/pfcp` | PFCP codec (SMF↔UPF) |
| `lib/gtp` | GTPv1/v2 codec |
| `lib/diameter` | Diameter stacks for 4G: S6a, Gx, Gy, S6b (via freeDiameter subproject) |
| `lib/crypt` | MILENAGE, AES, KDF (5G key derivation) |
| `lib/dbi` | MongoDB abstraction — one collection: `subscribers` |
| `lib/app` | YAML config parsing, application init helpers |

### MongoDB (`lib/dbi/`)

Single collection: `subscribers`. All DBI functions take a SUPI string and query/mutate this collection:
- `ogs_dbi_auth_info()` — fetches `K`, `OPc`/`OP`, `SQN` for authentication (called by AUSF)
- `ogs_dbi_subscription_data()` — fetches allowed PLMNs, DNN, operator config
- `ogs_dbi_session_data()` — navigates nested `slice[]→session[]→DNN` for QoS/charging rules
- `ogs_dbi_increment_sqn()` / `ogs_dbi_update_sqn()` — SQN management after auth

### Error handling conventions

- `ogs_assert(x)` — for internal preconditions that must never fail (crashes on failure, not a recoverable error path)
- `ogs_expect(x)` — like assert but logs and continues
- `OGS_OK` / `OGS_ERROR` — return values for fallible functions
- In protocol handlers receiving network input: log the error and `return`/send a reject message — do not assert on external data

### Coding style

4-space indentation, LF line endings (enforced by `.editorconfig`). No clang-format file; match the surrounding code style. License header required on all new `.c`/`.h` files (AGPL v3).

### Test config

Integration tests use `build/configs/sample.yaml` (generated at configure time). NFs are selectively disabled with `global.parameter.no_<nf>: true`. The test PLMN is MCC=999, MNC=70. NF loopback addresses follow the pattern `127.0.0.<N>:7777`; NRF is at `127.0.0.10`, SCP at `127.0.0.200`.
