# open5gs-camara — CAMARA API gateway for the Open5GS lab

A sidecar that exposes [CAMARA](https://camaraproject.org) network APIs on top of this
Open5GS 5GC lab. It runs as an AF / "mini-NEF": the APIs are served northbound, and it
calls the core southbound (SBI h2c, the SMF/AMF info endpoints, MongoDB).
Design, scope and phases: [PLAN.md](PLAN.md).

**Status:** Phase 5 — Application Profiles + Connectivity Insights; Phase 4 Device Roaming Status
(+ subscriptions); Phase 3 Number Verification; Phase 2 SIM/Device Swap (+ subscriptions);
Phase 1 QoS Profiles + Quality on Demand; Phase 0 foundations.

## Lab prerequisites (Phase 1)

1. PCF profile mapping: the `qos_profiles` block from `lab/pcf-qos-profiles.yaml` goes under `pcf:` in
   `install/etc/open5gs/pcf.yaml`. Then run `./open5gs-ctl.sh restart pcf` and **re-attach the UEs**
   (the PCF loses its SM policy contexts on restart, so QoD returns `SERVICE_NOT_APPLICABLE` until the
   PDU session is re-established).
2. PCC rule templates on each demo subscriber (one per profile 5QI, no flows):
   `.venv/bin/python tools/seed_pcc_rules.py --imsi 999700000000001` (idempotent; `--dry-run` to preview).
   Without them the PCF rejects the app-session with 403, which surfaces as `QUALITY_ON_DEMAND.QOS_PROFILE_NOT_APPLICABLE`.
3. The demo subscriber needs an MSISDN and an attached UE with a PDU session on `internet`.

## Install

Requires `uv` (`~/.local/bin/uv`), `jq`, `curl`, and the 5GC NFs running.

```bash
cd misc/camara
./setup.sh --dev                 # venv (Python 3.12) + deps + config.yaml with generated secrets
./setup.sh --specs               # (re)download pinned CAMARA OpenAPI specs into specs/
./setup.sh --install-service     # optional: systemd unit open5gs-camara.service (sudo)
```

`config.yaml` is git-ignored (mode 600) and holds the JWT signing key and the `demo-app`
client secret. See `config.example.yaml` for every option.

## Run

```bash
.venv/bin/python -m camara_gw            # foreground (Hypercorn), listens on 0.0.0.0:8090
sudo systemctl status open5gs-camara     # if installed as a service
curl -s localhost:8090/health | jq .     # 200 UP / 503 DOWN with per-dependency checks
```

## Endpoints (so far)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/health` | none | Mongo, NRF registration of BSF/PCF/UDM, SMF `/pdu-info`, AMF `/ue-info` |
| POST | `/oauth2/token` | client credentials (Basic or form) | `client_credentials` (2-legged) or `authorization_code` (+PKCE) → HS256 JWT (+ `id_token`) |
| GET | `/oauth2/authorize` | none — **network-based** | Must arrive over the UE data path; source IP → live PDU session → SUPI → code (no user interaction); else `error=access_denied` |
| GET | `/.well-known/openid-configuration` | none | OIDC discovery |
| POST | `/_lab/resolve` | Bearer, scope `lab:read` | Resolve a CAMARA `device` → SUPI, phone, UE IPs (lab only, `lab_endpoints: true`) |
| POST | `/qos-profiles/v1/retrieve-qos-profiles` | `qos-profiles:read` | List profiles (filter by name/status/device) |
| GET | `/qos-profiles/v1/qos-profiles/{name}` | `qos-profiles:read` | One profile |
| POST | `/quality-on-demand/v1/sessions` | `quality-on-demand:sessions:create` | Create QoD session → PCF app-session → dedicated QoS flow |
| GET / DELETE | `/quality-on-demand/v1/sessions/{id}` | `…:read` / `…:delete` | Get / release |
| POST | `/quality-on-demand/v1/sessions/{id}/extend` | `…:update` | Extend (capped at profile maxDuration) |
| POST | `/quality-on-demand/v1/retrieve-sessions` | `…:retrieve-by-device` | Sessions of a device |
| POST | `/_internal/pcf/qod/{id}/terminate` | loopback only | PCF → gateway termination callback (h2c) |
| POST | `/sim-swap/v2/retrieve-date`, `/sim-swap/v2/check` | `sim-swap:retrieve-date` / `sim-swap:check` (or `sim-swap`) | Latest SIM change / swapped within `maxAge` hours |
| POST | `/device-swap/v1/retrieve-date`, `/device-swap/v1/check` | `device-swap:…` (or `device-swap`) | Same for device (IMEI) changes |
| POST / GET | `/sim-swap-subscriptions/v0.4/subscriptions` | `…:org.camaraproject.sim-swap-subscriptions.v0.swapped:create` / `…:read` | Create / list (paged) |
| GET / DELETE | `/sim-swap-subscriptions/v0.4/subscriptions/{id}` | `…:read` / `…:delete` | Get / delete |
| POST | `/number-verification/v2/verify` | 3-legged `number-verification:verify` | `{phoneNumber}` or `{hashedPhoneNumber}` → `{devicePhoneNumberVerified}` |
| GET | `/number-verification/v2/device-phone-number` | 3-legged `number-verification:device-phone-number:read` | `{devicePhoneNumber}` |
| POST | `/device-roaming-status/v1/retrieve` | `device-roaming-status:read` | `{lastStatusTime, roaming, countryCode?, countryName?}` |
| POST / GET | `/device-roaming-status-subscriptions/v0.9/subscriptions` | `…:<event-type>:create` / `…:read` | roaming-status / roaming-on / roaming-off / roaming-change-country |
| GET / DELETE | `/device-roaming-status-subscriptions/v0.9/subscriptions/{id}` | `…:read` / `…:delete` | Get / delete |
| POST | `/_lab/roaming-override` | `lab:write` | Pin a device to a fake serving PLMN (`null` clears) |
| POST | `/application-profiles/v0.6/application-profiles` | `application-profiles:create` | Create profile (network thresholds; compute stored only) |
| GET / PATCH / DELETE | `/application-profiles/v0.6/application-profiles/{id}` | `…:read` / `…:update` / `…:delete` | PATCH replaces the full threshold set |
| POST | `/connectivity-insights/v0.7/check-network-quality` | `connectivity-insights:check` | Per-KPI "meets / unable to meet the application requirements" |
| GET | `/_lab/connectivity-insights/last` | `lab:read` | Raw measurements behind the last verdict |

### QoD behaviour

- `REQUESTED` on create, then `AVAILABLE` once SMF `/pdu-info` shows a new QoS flow with the profile's 5QI
  (within `qod.verify_timeout`). Otherwise `UNAVAILABLE`/`NETWORK_TERMINATED` and the app-session is released.
- Profile rates map to the PCF media component: `maxDown/UpstreamRate` → `marBwDl/Ul` (MBR),
  `targetMinDown/UpstreamRate` → `mirBwDl/Ul` (GBR). All lab profiles use GBR 5QIs (QOS_E = 3, S/M/L = 4).
- Flows: `applicationServer` (IPv4 address/subnet or `ipAddresses`) × ports → `permit out` (downlink) /
  `permit in` (uplink) IPFilterRules. With ports, explicit TCP and UDP rules are generated.
- One active session per device (409 `CONFLICT`). Sessions are visible only to the client that created them.
- Events: `org.camaraproject.quality-on-demand.v1.qos-status-changed` CloudEvents to `sink`.
  CAMARA requires https sinks; the lab allows http via `notify.allow_http_sink: true`.

### Swap behaviour

- A poller (`swap.poll_interval`, 2 s) snapshots `subscribers` and records changes in `camara_events`:
  **SIM_SWAP** when an MSISDN moves to a different IMSI (remove-then-add counts as one swap), and
  **DEVICE_SWAP** when the IMEI (first 14 digits of the IMEISV the UDR stores at registration) changes.
  A software-version-only change is not a device swap.
- History starts at the first poll (`camara_snapshots.monitoring_since`). With no swap since then,
  `retrieve-date` returns `null` + `monitoredPeriod` (days) and `check` returns `false`.
- Timestamps are detection times (±poll interval).
- SIM swap subscriptions: `swapped` CloudEvents `{phoneNumber, subscriptionId}`; `subscriptionExpireTime`,
  `subscriptionMaxEvents`, and `subscription-ended` on delete/expiry/max events. One event type per
  subscription; duplicate active subscriptions → 409 `ALREADY_EXISTS`.
- Lab provisioning helper: `tools/lab_swap.py show | add-spare | move-msisdn | set-imeisv`.

### Number Verification behaviour

- The device calls `http://10.45.0.1:8090/oauth2/authorize?...&scope=openid number-verification:verify` over
  its data path (`curl --interface uesimtun0` on the UERANSIM host). The gateway maps the source IP via SMF
  `/pdu-info` to the SUPI and redirects to the registered `redirect_uri` with a 60 s single-use code.
  Off-network or unknown IPs get `error=access_denied`; unregistered redirect URIs get a 400 with no redirect.
- The backend exchanges the code (`grant_type=authorization_code`, client secret, optional PKCE S256) for an access
  token with `sub`=SUPI, `amr=["network"]`, at most 300 s, **single use**, and no refresh token.
- NV scopes can't be obtained with `client_credentials`. A token without network auth gets
  403 `NUMBER_VERIFICATION.USER_NOT_AUTHENTICATED_BY_MOBILE_NETWORK`.
- `hashedPhoneNumber` = SHA-256 hex of the E.164 number including `+`.
- Lab note: traffic generated *on the VM* towards `10.45.0.1` is MASQUERADEd (it leaves via `lo`, matching
  `-s 10.45.0.0/16 ! -o ogstun`), so a UE can't be faked from the VM. Real UE traffic arrives on `ogstun`
  and keeps its source IP.

### Roaming behaviour

- Status comes from AMF `/ue-info` (`location.nr_tai.plmn`, `location.timestamp` → `lastStatusTime`). Roaming =
  serving PLMN ∉ `home_plmn`. `countryCode` = serving MCC; `countryName` = ISO list from `camara_gw/mcc.py`
  (a curated subset; `[]` for unknown/test MCCs). If the AMF no longer knows the UE, the last recorded status is
  returned; if it never did → 422 `SERVICE_NOT_APPLICABLE`.
- UERANSIM can't do real roaming, so `/_lab/roaming-override` simulates a visited PLMN (logged as a LAB OVERRIDE).
- A poller (`roaming.poll_interval`, 2 s) detects changes → `roaming-status` + `roaming-on/off` on transitions,
  `roaming-change-country` when the MCC changes while roaming. `initialEvent: true` fires immediately if the
  current state matches. One event type per subscription (multi → 422 `MULTIEVENT_SUBSCRIPTION_NOT_SUPPORTED`).

### Connectivity Insights behaviour

- The gateway pings the UE's PDU-session IP from the VM over `ogstun` (`ci.ping_count` × `ci.ping_interval`,
  about 4 s; unprivileged ping). One-way delay = median RTT / 2 (CAMARA PDB = device ↔ operator gateway);
  jitter = p99 of consecutive |ΔRTT|; loss compared with 10^-N (resolution 1/ping_count).
- Throughput KPIs are assessed only with `ci.iperf_enabled` (iperf3 on the VM + `iperf3 -s` on the UE host);
  otherwise they're **omitted**, not guessed. `computeResources` are stored but not evaluated.
- `monitoringTimeStamp` is accepted; measurements are always current (no prediction).
- The response uses the spec's field spelling `packetlossErrorRate` (the profile uses `packetLossErrorRate`).

All errors use the CAMARA `ErrorInfo` shape `{status, code, message}`. `x-correlator` is
validated and echoed back (generated if absent).

## Demo

```bash
./demo/00-foundations.sh                              # health, token, resolve by phone
PHONE=+99970000001 UE_IP=10.45.0.2 ./demo/00-foundations.sh

./demo/sink.py &                                       # CloudEvents receiver on :9099
PROFILE=QOS_E DURATION=120 ./demo/01-qod.sh             # profiles → create → AVAILABLE → extend → delete
./demo/02-swap.sh                                       # subscribe → SIM swap → device swap → restore
./demo/03-number-verification.sh                        # prints the device command; then token + verify
UE_SSH='ssh user@ueransim-host' ./demo/03-number-verification.sh   # fully automatic if SSH works
./demo/04-roaming.sh                                    # subscribe → "travel" FR → DE → home (lab override)
./demo/05-connectivity-insights.sh                      # lenient vs strict profile on the live UE
NETEM=1 ./demo/05-connectivity-insights.sh              # + tc netem on ogstun (sudo) → strict profile fails
```

For a successful resolve, the demo subscriber needs an MSISDN (WebUI → subscriber → MSISDN), and
for IP resolution the UE needs an active PDU session.

## Tests

```bash
.venv/bin/python -m pytest            # unit tests, core mocked (MockTransport + mongomock)
.venv/bin/python -m pytest -m live    # live smoke against the running lab (later phases)
```

## Implementation notes

- **Hypercorn, not uvicorn.** The PCF/SCP call back over h2c prior-knowledge; Hypercorn serves h2c and
  HTTP/1.1 on the same port.
- **SBI is h2c-only.** httpx must use `http1=False, http2=True` (prior knowledge). Plain
  `http2=True` sends HTTP/1.1 over cleartext, and the NFs drop it.
- **Don't use NRF discovery for health/lookup as an AF.** Open5GS filters `nnrf-disc` results by the
  target's `allowedNfTypes`, so UDM is invisible to an AF/NEF requester. The gateway uses
  `nnrf-nfm` listings and fixed NF addresses from `config.yaml`.
- `/pdu-info` and `/ue-info` are Open5GS-specific (metrics port, HTTP/1.1). All knowledge of their
  shape lives in `camara_gw/core/info.py`.
- The gateway never writes Open5GS collections; its own state goes in `camara_*` collections. Only the
  lab setup tool `tools/seed_pcc_rules.py` adds PCC-rule templates to `subscribers` (add-only).
- Helpers in `core/mongo.py` and `core/info.py` are adapted from `~/open5gs-mcp` (provenance noted
  in each file).
