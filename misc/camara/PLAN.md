# CAMARA API Demo on Open5GS — Plan

Status: **draft / planning** · Created 2026-11-19 · Target lab: edmiand fork, arm64 Ubuntu 22.04

## 1. Goal

Demonstrate a set of [CAMARA](https://camaraproject.org) network APIs served on top of
this Open5GS 5GC lab, backed by real core state wherever Open5GS can provide it:

| # | API | CAMARA repo @ pinned release | Spec file(s) |
|---|-----|------------------------------|--------------|
| 1 | QoS Profiles | QoSProfiles @ `r1.2` | `qos-profiles.yaml` |
| 2 | Quality on Demand | QualityOnDemand @ `r4.3` | `quality-on-demand.yaml` (`qos-provisioning.yaml` = stretch) |
| 3 | SIM Swap (+ Subscriptions) | SimSwap @ `r4.3` | `sim-swap.yaml`, `sim-swap-subscriptions.yaml` |
| 4 | Device Swap | DeviceSwap @ `r4.3` | `device-swap.yaml` |
| 5 | Number Verification | NumberVerification @ `r4.2` | `number-verification.yaml` |
| 6 | Device Roaming Status | DeviceRoamingStatus @ `r2.3` | `device-roaming-status.yaml` |
| 7 | Device Roaming Status Subscriptions | DeviceRoamingStatus @ `r2.3` | `device-roaming-status-subscriptions.yaml` |
| 8 | Connectivity Insights | ConnectivityInsights @ `r4.3` | `connectivity-insights.yaml` (`-subscriptions` = stretch) |
| 9 | Application Profiles (needed by #8) | ApplicationProfiles @ `r2.3` | `application-profiles.yaml` |

Release tags were the latest on GitHub as of 2026-11-19. Specs are vendored into
`misc/camara/specs/<repo>@<tag>/` so the demo doesn't drift when CAMARA publishes new releases.

### Non-goals

- Production-grade exposure: no CAPIF onboarding, no multi-tenant consent management, no rate limiting beyond basics.
- Changing Open5GS C code. Everything runs alongside the core (a sidecar). Revisit only if a gap can't be closed otherwise (see §9).
- 4G EPC. 5GC only, in line with the lab constraints.
- Real inter-PLMN roaming. Upstream docs state roaming can't be tested with UERANSIM (`docs/_docs/tutorial/05-roaming.md`).

## 2. Key decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Core | Keep Open5GS (this fork) | free5GC has no published CAMARA code. Its NEF lacks AsSessionWithQoS, and there's no SEPP (no roaming). A gateway is needed either way, and migrating would cost an extra 3–5 days and the existing lab tooling. |
| Integration style | **CAMARA gateway sidecar** acting as AF / "mini-NEF" | No NEF in Open5GS. A sidecar avoids touching C code and upstream merges. |
| Language / stack | Python 3.12 (via `uv`), FastAPI, uvicorn, httpx[http2], pymongo, PyJWT | Matches the sibling `~/open5gs-mcp` project (same `uv` toolchain and deps). The system Python 3.10 lacks `python3-venv`. |
| Location | `misc/camara/` in this repo | Keeps the demo versioned with the core it targets. |
| Run model | systemd unit `open5gs-camara.service`, venv under `misc/camara/.venv` | Same as the `open5gs-mcp` / `open5gs-webui` units. |
| Listen | `0.0.0.0:8090` (HTTP in the lab; TLS optional later) | Must be reachable from the UE data path at `10.45.0.1` (Number Verification) and from the LAN. |
| Event delivery | Polling the core + CloudEvents to the subscriber `sink` | Open5GS has no UDM-EE / AMF event exposure. A 2–5 s poll is fine for a demo. |
| Auth | **Decided:** gateway-issued JWTs: `client_credentials` for 2-legged calls; a minimal network-based `/authorize` for Number Verification. No Keycloak. | Easiest for a demo: no extra service on the arm64 VM. Keycloak can be swapped in later behind the same token endpoints. |
| Exposure | **Decided:** lab network only, plain HTTP | Not exposed via Tailscale for now. Add TLS before any wider exposure. |
| Demo client | **Decided:** curl + jq scripts in `demo/` | No web UI for now. |
| Code reuse with `~/open5gs-mcp` | **Decided:** copy the Mongo/subscriber helpers (`_subscriber_util.py`) and the `/pdu-info`·`/ue-info` paging (`list_ue_sessions.py`) into `camara_gw/core/`, with a provenance header | ~440 lines, same author. Keeps each project independently deployable. Extract a shared package only if a third consumer appears or the copies drift. |
| Device identification | 2-legged: `device` object in the request body (phoneNumber or ipv4Address) | Simplest for a demo. 3-legged (device from token) only for Number Verification. |

## 3. Architecture

```
                 ┌─────────────────────── lab VM ───────────────────────────────┐
 Demo app /      │  open5gs-camara (FastAPI :8090)                               │
 curl / Postman ─┼─▶ ┌───────────┐  ┌────────────┐  ┌─────────────┐              │
                 │   │ northbound│─▶│ resolvers  │─▶│ southbound  │              │
 UE (UERANSIM) ──┼─▶ │ CAMARA    │  │ phone↔SUPI │  │ adapters    │              │
 via uesimtun0   │   │ routers + │  │ ↔UE IP     │  │             │              │
 → 10.45.0.1     │   │ auth      │  └────────────┘  └──────┬──────┘              │
                 │   └─────┬─────┘                         │                     │
                 │         │ pollers (swap history,        │                     │
                 │         │ roaming, QoD expiry)          │                     │
                 │         ▼                               ▼                     │
                 │   MongoDB open5gs.{subscribers,   SBI (h2c): BSF .15  PCF .13 │
                 │     camara_events, camara_*}      UDM .12  NRF .10            │
                 │                                   HTTP: SMF .4:9090/pdu-info  │
                 │                                         AMF .5:9090/ue-info   │
                 │                                         UPF .7:9090/metrics   │
                 └───────────────────────────────────────────────────────────────┘
```

### Southbound interface inventory (verified in this fork)

| Source | Endpoint / object | Gives us | Used by | Code ref |
|--------|-------------------|----------|---------|----------|
| MongoDB `open5gs.subscribers` | `imsi`, `msisdn[]`, `imeisv`, slice/session/PCC rules | phone↔SUPI, current IMEISV (written by UDR on every registration) | all, SIM/Device Swap | `lib/dbi/subscription.c`, `src/udr/nudr-handler.c:354` |
| SMF `127.0.0.4:9090/pdu-info` (HTTP/1.1, paged) | `supi`, `ipv4`, `ipv6`, `dnn`, `snssai`, `qos_flows[{qfi,5qi}]`, `pdu_state` | SUPI→UE IP, live QoS flows (QoD proof) | QoD, NV, CI, resolver | `src/smf/pdu-info.c` |
| AMF `127.0.0.5:9090/ue-info` (HTTP/1.1, paged) | `supi`, `msisdn`, `pei`, `cm_state`, `nr_tai` (serving PLMN+TAC), `last_visited_plmn_id`, `lbo_roaming_allowed` | registration state, live PEI, serving PLMN | Roaming, Device Swap (live check) | `src/amf/ue-info.c` |
| BSF SBI `127.0.0.15:7777` | `GET /nbsf-management/v1/pcfBindings?ipv4Addr=` | IP→{supi,gpsi,dnn,snssai,PCF address}. **Lookup by IP only.** | QoD, NV | `src/bsf/bsf-sm.c:200` |
| PCF SBI `127.0.0.13:7777` | `POST /npcf-policyauthorization/v1/app-sessions`, `POST …/{id}/delete`, terminate notify → `notifUri` | dedicated QoS flow via `qosReference`→5QI | QoD | `src/pcf/npcf-handler.c:24,761,695` |
| PCF config | `qos_profiles: [{reference, qos_index}]` in `pcf.yaml` | maps CAMARA profile names → 5QI | QoD | `install/etc/open5gs/pcf.yaml:35` |
| UDM SBI `127.0.0.12:7777` | `GET /nudm-uecm/v1/{supi}/registrations/amf-3gpp-access`, `GET /nudm-sdm/v2/{supi}/am-data` | serving GUAMI/PLMN; GPSI list | Roaming (cross-check), NV | `src/udm/ue-sm.c:185` |
| UPF `127.0.0.7:9090/metrics` | Prometheus `fivegs_ep_n3_gtp_*`, `upf_qosflows` | traffic volume per QoS level | Connectivity Insights | `src/upf/metrics.c` |
| Data plane | ICMP / iperf3 from VM over `ogstun` to UE IP | latency, jitter, loss, throughput | Connectivity Insights | — |

The core's internal SBI is **h2c only** (HTTP/2 without TLS; see `kuma-push.sh`). The SMF/AMF/UPF `:9090` endpoints
are plain HTTP/1.1.

**Phase 0 findings (verified live 2026-11-19):**
- httpx speaks h2c only with `http1=False, http2=True` (prior knowledge). `http2=True` alone fails ("illegal request line").
- `GET /nudm-uecm/v1/{supi}/registrations/amf-3gpp-access` works and also returns `pei`.
- NRF *discovery* (`nnrf-disc`) with requester AF/NEF/PCF returns **no UDM** (filtered by UDM's `allowedNfTypes`). Use `nnrf-nfm` listings + fixed addresses instead; don't rely on discovery as an AF.

## 4. Per-API design summary

Detailed call flows: see §11 (taken from the design discussion).

| API | Northbound ops | Southbound | Real vs emulated |
|-----|----------------|------------|------------------|
| QoS Profiles | `POST /retrieve-qos-profiles`, `GET /qos-profiles/{name}` (per spec version) | static `profiles.yaml`, cross-checked at startup against `pcf.yaml qos_profiles` | Real (catalog) |
| Quality on Demand | `POST/GET/DELETE /sessions`, `POST /sessions/{id}/extend`, `POST /retrieve-sessions` | resolver → BSF → PCF app-session; expiry timer → PCF delete; PCF terminate → CloudEvent; verify via SMF `pdu-info.qos_flows` | **Real**: dedicated QoS flow installed in SMF/UPF/gNB/UE |
| SIM Swap | `POST /retrieve-date`, `POST /check` | poller diff `msisdn↔imsi` → `camara_events` | Real data, gateway-derived history |
| Device Swap | `POST /retrieve-date`, `POST /check` | poller diff `imeisv` per IMSI (+ AMF `pei`) → `camara_events` | Real data, gateway-derived history |
| Number Verification | `POST /verify`, `GET /device-phone-number` | network-based `/authorize` (source IP → BSF/SMF → SUPI) → token; MSISDN from Mongo/UDM | Real (network-based auth over UE data path) |
| Roaming Status | `POST /retrieve` | AMF `ue-info` serving PLMN vs SUPI home PLMN; MCC→ISO country | Real lookup; always `roaming:false` in a single-PLMN lab; **lab override** for demo |
| Roaming Subscriptions | `POST/GET/DELETE /subscriptions` | roaming poller → CloudEvents `roaming-status`, `roaming-on`, `roaming-off`, `roaming-change-country`, `subscription-ends` | Emulated push (polling) |
| Application Profiles | CRUD `/application-profiles` | Mongo `camara_app_profiles` | Gateway-only store |
| Connectivity Insights | `POST /check-network-quality` | resolver → active probe (ping/iperf3) + UPF metrics → threshold compare | Measured, not 3GPP NWDAF analytics |

### Gateway-owned MongoDB collections (in the `open5gs` DB, prefixed `camara_`)

`camara_events` (swap history), `camara_snapshots` (poller state), `camara_qod_sessions`,
`camara_subscriptions`, `camara_app_profiles`, `camara_clients` (API clients/secrets),
`camara_overrides` (lab roaming override). None of them touch Open5GS's own collections. Reads of
`subscribers` are read-only.

## 5. Repository layout

```
misc/camara/
├── PLAN.md                     # this document
├── README.md                   # install / run / demo walkthrough (Phase 0)
├── requirements.txt
├── open5gs-camara.service.in   # systemd unit template (installed by setup.sh --install-service)
├── setup.sh                    # venv, deps, unit install, pcf.yaml qos_profiles check
├── config.example.yaml         # endpoints, poll intervals, home PLMN, clients
├── specs/<Repo>@<tag>/*.yaml   # vendored CAMARA OpenAPI + test_definitions/*.feature
├── camara_gw/
│   ├── app.py                  # FastAPI app, router mounting, lifespan (pollers)
│   ├── config.py
│   ├── errors.py               # CAMARA ErrorInfo {status, code, message}, x-correlator
│   ├── auth/                   # token endpoint, JWT verify, scopes, NV /authorize
│   ├── core/                   # southbound adapters
│   │   ├── sbi.py              # h2c client (BSF, PCF, UDM, NRF)
│   │   ├── info.py             # SMF /pdu-info, AMF /ue-info clients
│   │   ├── mongo.py
│   │   ├── metrics.py          # UPF Prometheus scrape
│   │   └── resolver.py         # phone ↔ SUPI ↔ UE IP
│   ├── pollers/                # swap_history.py, roaming.py
│   ├── notify.py               # CloudEvents sink delivery (+ retry)
│   └── apis/                   # one router per CAMARA API
│       ├── qos_profiles.py  qod.py  sim_swap.py  device_swap.py
│       ├── number_verification.py  roaming.py  roaming_subs.py
│       └── app_profiles.py  connectivity_insights.py
├── demo/                       # scripted scenarios (bash + curl/jq), sample sink receiver
└── tests/                      # pytest unit (mocked core) + live smoke (marked)
```

## 6. Phases

Estimates are engineer-days. Each phase ends with a working demo script in `demo/` and a README section.

### Phase 0 — Foundations (2–3 d) — ✅ implemented 2026-11-19 (live resolver check pending a registered UE with MSISDN)
- Scaffold `misc/camara/`: FastAPI app, config, systemd unit, `setup.sh`, `open5gs-ctl.sh` hook (optional `camara` target).
- **Spike: h2c from Python.** ✅ httpx with `http1=False, http2=True` works against NRF/BSF/UDM; no fallback needed.
- Southbound adapters + resolver (phone → SUPI via Mongo; SUPI → IP via SMF `pdu-info`; IP → SUPI via BSF).
- CAMARA common pieces: `x-correlator`, `ErrorInfo` codes (`INVALID_ARGUMENT`, `IDENTIFIER_NOT_FOUND`, `SERVICE_NOT_APPLICABLE`, …), phone-number E.164 validation, `device` object handling.
- Auth v1: `POST /oauth2/token` (client_credentials), JWT verify middleware, per-API scopes.
- Vendor the specs; validate responses against the OpenAPI schemas in tests.
- Kuma: gateway `/health` check — deferred to Phase 6 (keep core health and demo health separate).

**Done when:** `GET /health` shows every southbound reachable, and `resolver` maps the demo UE's phone → SUPI → IP.

### Phase 1 — QoS Profiles + Quality on Demand (2–3 d) — ✅ core-verified live 2026-11-20 (⚠️ RAN issue, see below)
- `profiles.yaml`: QOS_E/S/M/L → 5QI + bitrates + duration limits; add matching `qos_profiles` to `install/etc/open5gs/pcf.yaml`.
- Add per-subscriber PCC rules (one per 5QI used) for demo subscribers. Script it via Mongo, not by hand in the WebUI.
- QoD sessions: create → BSF → PCF app-session (qosReference, marBw, fDescs from `applicationServer`/ports); expiry timer; extend; delete; `notifUri` callback → CloudEvent `qos-status-changed`.
- Demo: iperf3 before/after; show the new QFI/5QI in SMF `pdu-info` + SMF/PCF logs.

**Done when:** a QoD session creates a dedicated QoS flow visible in `pdu-info`, and it's removed on expiry/delete.

Phase 1 implementation notes (2026-11-19):
- Lab profile → 5QI: QOS_E = 3, QOS_S/M/L = 4 (all GBR). `targetMin*Rate` → `mirBw*` (GBR), `max*Rate` → `marBw*` (MBR). The PCF's SMF path forces GBR to the NGAP maximum when only MBR is given, so both are always sent.
- PCF requires a PCC rule with the same 5QI on the subscriber's session. Flowless templates are seeded by `tools/seed_pcc_rules.py`; the PCF skips flowless rules at PDU session setup.
- **Restarting the PCF drops its SM policy contexts.** BSF bindings stay, but app-session create returns 404 until the UE re-establishes its PDU session. The gateway maps this to 422 `SERVICE_NOT_APPLICABLE`.
- The BSF binding carries no `supi`/`gpsi` in Open5GS (only the PCF endpoint). IP → SUPI stays with SMF `/pdu-info`.
- The PCF terminate callback arrives as h2c → the server switched from uvicorn to Hypercorn.
- Commonalities: with several device identifiers the provider uses one (phone > IPv4 > IPv6) without cross-checking; `IDENTIFIER_MISMATCH` was removed from the resolver.
- CAMARA requires https sinks; the lab allows http (`notify.allow_http_sink`).

Live result (2026-11-19/20, QOS_E, AS 198.51.100.10):
- The PCF built a PCC rule with 5QI 3, MBR 2 Mbps / GBR 1 Mbps, flows to and from the AS. The SMF added **QFI 2 / 5QI 3** next to the default QFI 1 / 5QI 9 → `AVAILABLE` in ~1 s, CloudEvent delivered. Extend and retrieve-sessions worked.
- At expiry the gateway deleted the app-session, the SMF removed QFI 2, and the sink got `UNAVAILABLE / DURATION_EXPIRED`.
- ⚠️ **RAN:** when the AMF sent `PDUSessionResourceModifyRequest`, the gNB at 192.168.64.3 closed its SCTP association within 1 ms (AMF: "gNB-N2 connection refused", 0 gNBs, UE idle). The core side is correct. The RAN must support PDU Session Resource Modify for a full end-to-end demo. Open: identify the gNB and its failure (UERANSIM is suspected, not confirmed).

### Phase 2 — SIM Swap + Device Swap (+ SIM Swap Subscriptions) (2.5–3 d)
- Poller (2 s) snapshots `msisdn↔imsi` and `imeisv` per IMSI → `camara_events` with timestamps. Seed the baseline on first run.
- `retrieve-date` / `check` (maxAge hours). Return `latestSimChange: null` and `monitoredPeriod` per spec when no history exists.
- SIM Swap Subscriptions: create/list/get/delete; the poller emits CloudEvent `org.camaraproject.sim-swap-subscriptions.v0.swapped` to the `sink`. This introduces `notify.py` + `demo/sink.py` here (Phase 4 then reuses them).
- Demo: move an MSISDN between two IMSIs (script, mirrors a WebUI edit); change `imeiSv` in the UERANSIM UE config and re-register; the sink prints the "swapped" alert live.

**Done when:** both `check` endpoints flip from `false` to `true` after the scripted swap, and the subscribed sink receives the `swapped` event.

### Phase 3 — Number Verification (2–3 d)
- Minimal network-based authorization: `GET /authorize` on `10.45.0.1:8090` that identifies the caller by source IP (SMF `pdu-info` / BSF) and issues an auth code → `POST /oauth2/token` (authorization_code) → token with `sub`=SUPI.
- `POST /verify` (plain + `hashedPhoneNumber` SHA-256), `GET /device-phone-number`.
- Ensure no NAT between UE and gateway: the gateway is on the VM and the target is `10.45.0.1`, which is not MASQUERADEd.
- Demo: `curl --interface uesimtun0` through the flow; wrong number → `false`; call from off-network → `403`.

**Done when:** the true/false/off-network cases all behave per the CAMARA test definitions.

### Phase 4 — Device Roaming Status + Subscriptions (2 d)
- Roaming = AMF `ue-info.nr_tai.plmn` ≠ home PLMN (`999/70`) for that SUPI. Lab override in `camara_overrides` (`{supi, roaming, mcc}`), clearly flagged in responses' logs and README.
- MCC → ISO 3166 country table (vendored).
- Subscriptions: create/list/get/delete, `subscriptionExpireTime`, `subscriptionMaxEvents`, `initialEvent`; roaming poller → CloudEvents to `sink`; `subscription-ends` on expiry/delete.
- Reuse `notify.py` / `demo/sink.py` from Phase 2.

**Done when:** toggling the override fires `roaming-on`, then `roaming-change-country`, then `roaming-off`, and the sink receives them.

### Phase 5 — Application Profiles + Connectivity Insights (3 d)
- Application Profiles CRUD (Mongo).
- Probe engine: ping (count/interval) for latency, jitter and loss to the UE IP; optional iperf3 (needs `iperf3 -s` on the UE side) for throughput. UPF metrics for context.
- `check-network-quality`: compare against profile thresholds → `connectivityQualityResult` per spec.
- Demo: same UE, two profiles (lenient vs strict); then add `tc netem` delay on `ogstun` to show it flip.

**Done when:** a strict profile flips to "does not meet" under injected netem delay.

### Phase 6 — Demo packaging (2 d)
- `demo/run-all.sh` scripted walkthrough; Postman/Bruno collection; README "Demo script" section.
- Run the CAMARA Gherkin `Test_definitions` that apply (as checklists, or automated where cheap).
- Optional: one-page architecture/sequence diagram artifact for presentations.

### Phase 7 — Optional stretch (2–2.5 d)
- QoS Provisioning (`qos-provisioning.yaml`): a persistent per-device profile = PCF app-session without a duration and with a match-all flow filter (~1 d). Low extra demo value; it looks like QoD.
- Connectivity Insights Subscriptions: a per-subscription probe scheduler + threshold-crossing CloudEvents (~1–1.5 d).

**Total: ~16–19 engineer-days** (+2–2.5 d if Phase 7 is done). Phases 1–3 are the core demo (~9–12 d) and can be shown independently.

## 7. Lab prerequisites / config changes

| Change | Where | Phase |
|--------|-------|-------|
| `qos_profiles` QOS_E/S/M/L → 5QI | `install/etc/open5gs/pcf.yaml` (restart PCF) | 1 |
| PCC rules for each demo subscriber (5QI per profile) | Mongo `subscribers` (scripted) | 1 |
| Two demo subscribers with distinct MSISDNs (+ a spare IMSI for SIM swap) | Mongo / WebUI | 0 |
| UERANSIM UE config with `imeiSv` editable; `iperf3` on the UE host | UERANSIM host | 1, 2, 5 |
| Gateway reachable at `10.45.0.1:8090` from the UE; firewall allows it | iptables (sudo) | 3 |
| Python venv + deps; systemd unit | `misc/camara/setup.sh` (sudo only for the unit) | 0 |
| Optional: `tc netem` for the CI demo | sudo | 5 |

These follow the lab rules in `CLAUDE.md`: non-interactive, `sudo` only where required, fail loudly.

## 8. Testing

- **Unit:** pytest with mocked southbound (respx for httpx, mongomock). Validate every response against the vendored OpenAPI schema.
- **Conformance:** map each CAMARA `Test_definitions/*.feature` scenario to a test or a manual checklist item.
- **Live smoke** (`pytest -m live`): needs NFs up + a registered UERANSIM UE. Exercises one happy path per API.
- **Core regression:** none expected (no C changes). `meson test` remains the gate for upstream merges.

## 9. Gaps, risks, mitigations

| Gap / risk | Impact | Mitigation |
|-----------|--------|------------|
| No event exposure (UDM-EE / AMF-EE) | Notifications arrive by polling (seconds late) | Poll at 2–5 s; document it. |
| No real roaming with UERANSIM | Roaming always `false` | Labelled lab override; real roaming would need a second PLMN and a non-UERANSIM RAN. |
| No NWDAF | Connectivity Insights isn't 3GPP analytics | Active probing + UPF metrics; state it in the demo. |
| No swap history in the core | SIM/Device swap dates exist only once the poller starts | Seed the baseline at install; history starts then. A small optional C patch could log `imeisv` changes with timestamps (not planned). |
| PCF needs a PCC rule per 5QI per subscriber | QoD returns 403 otherwise | Script the PCC rules; the gateway maps 403 to a CAMARA error with a hint. |
| ~~h2c client support in Python~~ | — | Resolved in Phase 0: httpx `http1=False, http2=True`. |
| BSF binding appears only after a PDU session with PCF | IP lookups fail for idle/unattached UEs | The resolver also checks SMF `pdu-info`; return `SERVICE_NOT_APPLICABLE` / `UNNECESSARY_IDENTIFIER` per spec. |
| `/pdu-info` / `/ue-info` are Open5GS-specific, not 3GPP | Upstream may change them | Isolate in `core/info.py`; cover with a live smoke test after each `open5gs-upgrade.sh`. |
| Upstream merges touching `pcf.yaml` | Config conflicts | Keep the `qos_profiles` block small; note it in the README upgrade section. |
| Spec churn (CAMARA releases ~2x/year) | Field renames | Pin tags (§1); upgrade deliberately. |

## 10. Open questions

Resolved 2026-11-19:
- Auth = gateway-issued tokens (no Keycloak).
- Exposure = lab network only.
- Demo client = curl scripts.
- Stretch = SIM Swap Subscriptions in Phase 2; QoS Provisioning and Connectivity Insights Subscriptions deferred to optional Phase 7.
- Code reuse = copy helpers from `~/open5gs-mcp` with a provenance header.

No open questions at present.

## 11. Reference call flows

Shared front half for every API:

```
App ─(1) POST /oauth2/token (client_credentials) ─▶ Gateway auth   → JWT
App ─(2) CAMARA call, Bearer JWT, x-correlator ──▶ Gateway: verify JWT + scope
Gateway ─(3) resolve device: phone → SUPI (Mongo) → UE IP (SMF pdu-info)
         ...API-specific southbound...
Gateway ─▶ App: CAMARA JSON or ErrorInfo, x-correlator echoed back
```

**Quality on Demand**
```
App ─▶ POST /quality-on-demand/vX/sessions {device, applicationServer, qosProfile:"QOS_E", duration}
GW  ─▶ BSF  GET /nbsf-management/v1/pcfBindings?ipv4Addr=<UE IP>   ◀ {supi, dnn, snssai, pcf}
GW  ─▶ PCF  POST /npcf-policyauthorization/v1/app-sessions
            {ascReqData:{ueIpv4, notifUri, suppFeat, medComponents:{"1":{qosReference:"QOS_E",
             marBwDl, marBwUl, medSubComps:{"1":{fDescs:[...]}}}}}}       ◀ 201 Location
    PCF ─▶ SMF (SM policy update notify) ─▶ UPF (PFCP mod) + AMF ─▶ gNB ─▶ UE (new QoS flow)
GW  ◀── 201 {sessionId, qosStatus, expiresAt}
expiry / DELETE ─▶ PCF POST …/app-sessions/{id}/delete ─▶ CloudEvent qos-status-changed → sink
```

**SIM Swap / Device Swap**
```
poller: subscribers snapshot diff (msisdn↔imsi | imeisv) ─▶ camara_events {type, msisdn, old, new, ts}
App ─▶ POST /sim-swap|device-swap/vX/retrieve-date|check {phoneNumber[, maxAge]}
GW  ─▶ camara_events (latest for msisdn) ◀ {latestSimChange|latestDeviceChange} | {swapped}
```

**Number Verification**
```
UE (via uesimtun0) ─▶ GET 10.45.0.1:8090/authorize?...  (source IP = UE IP)
GW ─▶ SMF pdu-info / BSF: IP → SUPI ─▶ auth code ─▶ app backend ─▶ POST /oauth2/token ─▶ token(sub=SUPI)
App backend ─▶ POST /number-verification/vX/verify {phoneNumber|hashedPhoneNumber}
GW: SUPI → MSISDN (Mongo or UDM nudm-sdm am-data gpsis) ─▶ compare ◀ {devicePhoneNumberVerified}
```

**Device Roaming Status (+ Subscriptions)**
```
App ─▶ POST /device-roaming-status/vX/retrieve {device}
GW  ─▶ AMF ue-info (serving PLMN) vs home PLMN [+ lab override] ◀ {roaming, countryCode, countryName}
poller ─▶ same check every N s ─▶ on change: CloudEvent roaming-on|off|change-country ─▶ sink
```

**Application Profiles + Connectivity Insights**
```
App ─▶ POST /application-profiles/vX/application-profiles {networkQualityThresholds} ◀ {applicationProfileId}
App ─▶ POST /connectivity-insights/vX/check-network-quality {device, applicationProfileId, applicationServer}
GW  ─▶ resolve UE IP ─▶ ping/iperf3 over ogstun + UPF metrics ─▶ compare ◀ {connectivityQualityResult}
```
