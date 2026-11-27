# open5gs-camara — development log

Working notes to resume work. Design and scope: [PLAN.md](PLAN.md) · usage: [README.md](README.md) ·
presenting: [demo/POC-RUNBOOK.md](demo/POC-RUNBOOK.md) · conformance: [CONFORMANCE.md](CONFORMANCE.md).

## Current state (end of 2026-11-20)

- **Branch:** `camara-demo` (local dev; also pushed to `origin` = edmiand/open5gs as a backup). **No PR**: local
  development stage. 12 commits on top of `main` (`c5bd0b6a2` … `e668a988f`, plus this log).
- **Gateway:** v0.8.0, systemd `open5gs-camara.service` active on `0.0.0.0:8090` (Hypercorn, HTTP/1.1 + h2c).
- **Tests:** 169 unit tests pass (`.venv/bin/python -m pytest`). Conformance: 483 ✅ / 15 ⚠️ / 0 ⛔ / 2 ➖ of 500
  CAMARA test-definition scenarios.
- **Lab:** `./demo/preflight.sh` all green (UE `imsi-999700000000001` / `+99970000001`, IP 10.45.0.12, CM-CONNECTED;
  gNB on N2; event sink on :9099).
- **Architecture page** (private, share via its menu): https://claude.ai/artifact/XwGBDwCBrA3ReJKTreT5DH

### APIs served (versions = CAMARA releases pinned in `specs/`)

| Story | API | Version | Repo@tag |
|---|---|---|---|
| 1 Identity | Number Verification | 2.1.1 | NumberVerification@r4.2 |
| 1 Identity | Device Identifier | 0.4.0 | DeviceIdentifier@r3.5 |
| 1 Identity | Device Swap | 1.1.0 | DeviceSwap@r4.3 |
| 2 Location | Device Roaming Status + Subscriptions | 1.2.0 / 0.9.0 | DeviceRoamingStatus@r2.3 |
| 3 Quality | Quality on Demand, QoS Profiles | 1.2.0 / 1.2.0 | QualityOnDemand@r4.3, QoSProfiles@r1.2 |
| 3 Quality (opt.) | Connectivity Insights + Application Profiles | 0.7.0 / 0.6.0 | ConnectivityInsights@r4.3, ApplicationProfiles@r2.3 |
| extra | SIM Swap + Subscriptions | 2.2.0 / 0.4.0 | SimSwap@r4.3 |

## Resume checklist

1. **Before presenting (2026-11-21):**
   - Start the sink with `python3 misc/camara/demo/sink.py` in its own terminal.
   - Run `./misc/camara/demo/preflight.sh` and fix whatever it reports.
   - If the UE went idle overnight: restart `nr-ue` on the UERANSIM host (192.168.64.3).
   - If swap history has rehearsal residue: `.venv/bin/python tools/lab_swap.py reset-history`.
2. Present with `./misc/camara/demo/stories.sh` (all three stories in sequence) or by hand from `demo/POC-RUNBOOK.md`: Story 1 → 2 → 3. Story 3 goes **last**; afterwards restart `nr-gnb` + `nr-ue`.
3. Not yet seen live: **Story 1 device step (1b)** on the UERANSIM host (`curl --interface uesimtun0 …/oauth2/authorize`).
   Rehearse with `./demo/story1-identity.sh` (no QoD, the gNB stays up).
4. After the presentation, pick up the open decisions below.

## Open decisions / backlog

| Item | Status | Notes |
|---|---|---|
| Story 3 RAN | **user deciding** | UERANSIM's gNB drops N2 on `PDUSessionResourceModifyRequest` → QoD verified in the core only (UERANSIM issue #344: the maintainer confirms Modify isn't supported). Researched 2026-11-20, see "RAN options for Story 3" below. |
| ES256 + JWKS token signing | deferred (recommended) | Lets the EQTY gateway verify `id_token` (`sub`, `amr=network`) itself, like a real carrier. ~0.5 d. |
| Gateway audit log | deferred (recommended) | client, API, hashed subject, result, `x-correlator`; EQTY passes its decision id as `x-correlator`. ~0.5 d. |
| Signed API responses | not recommended | Not CAMARA; no carrier offers it. |
| EQTY gateway mock | not now (user) | POC is a manual curl run. |
| Connectivity Insights netem run | pending (needs sudo) | `NETEM=1 ./demo/05-connectivity-insights.sh` |
| iperf3 throughput KPIs | optional | `sudo apt install iperf3` on the VM, `iperf3 -s` on the UE host, `ci.iperf_enabled: true`. |
| Kuma monitor for the gateway | optional | `kuma-push-camara.sh` + `~/.config/open5gs-camara-kuma.env` + cron. |
| Story 1 decision flow | decided: **flow B** | NV 3-legged (1 redirect) → Device Identifier `match-identifier` 2-legged by verified MSISDN → Device Swap `check`. |

## RAN options for Story 3 (researched 2026-11-20, not yet tested)

1. **OAI gNB + nrUE in RFsimulator** (software-only): the first candidate. Source on GitHub:
   **https://github.com/duranta-project/openairinterface5g** (branch `develop`, primary; `openairinterface/openairinterface5g`
   is a read-only mirror of it). Checked 2026-11-20: the gNB decodes `PDUSessionResourceModifyRequest` incl.
   QosFlowAddOrModify/Release lists (`openair3/NGAP/ngap_gNB_handlers.c`, `rrc_gNB_process_NGAP_PDUSESSION_MODIFY_REQ`);
   nrUE PDU Session Modification + UL QoS-flow matching merged in PR #432 (2026-10-09). Open: PR #831 (E1AP DRBs on modify,
   only matters for a split CU-CP/CU-UP setup; use a monolithic gNB) and PR #1131 (nrUE initial QoS rules). Not yet tested with
   Open5GS here; check that it builds/performs on the target host's architecture. Spike: ~0.5–1 d.
   Setup prompt for a Claude session on VM2: `lab/OAI-VM2-SETUP-PROMPT.md` (OAI UE uses spare SIM 999700000000002 / +99970000002).
2. **srsRAN Project gNB + srsUE over ZMQ**: needs `qos.yml` entries for the 5QIs used (3 and 4). Modify support with srsUE
   is not confirmed. Fallback.
3. **OMEC gnbsim**: PDU session modification merged Sep 2026 (PR #520); generates UL user-plane traffic; validated on
   SD-Core, not Open5GS. A profile-driven test tool, not a persistent UE with a tun interface → proves the signalling, not a
   live video demo.
4. **Patch UERANSIM** (gNB: answer PDU Session Resource Modify; UE: accept the NAS modification command): keeps the
   current setup; est. 1–2 d C++; risky.
5. **Amarisoft** (commercial): known to work, costs money.

## Lab changes outside the repo (to know / undo)

- `install/etc/open5gs/pcf.yaml`: `qos_profiles` QOS_E→5QI 3, QOS_S/M/L→5QI 4 (copy in `lab/pcf-qos-profiles.yaml`).
  The PCF was restarted (restarting it drops policy contexts, so the UE must re-attach).
- `subscribers` `999700000000001`: MSISDN `99970000001` (set by the user); flowless PCC rules for 5QI 3 and 4
  (`tools/seed_pcc_rules.py`).
- `subscribers` `999700000000002`: spare SIM cloned from …001 with no MSISDN (`tools/lab_swap.py add-spare`).
- Gateway-owned Mongo collections (`camara_*`): events, snapshots, QoD sessions, subscriptions, app profiles, auth codes,
  used tokens, overrides, roaming state. Swap history was reset on 2026-11-20 (rehearsal residue).
- `/etc/systemd/system/open5gs-camara.service` (installed by the user with `setup.sh --install-service`).
- `config.yaml` (git-ignored, mode 600): JWT signing key, `demo-app` and `guardian` secrets. Backup before the
  `guardian` client was added: `config.yaml.bak-20261126-160519` (git-ignored, mode 600).
- **Tailscale Funnel** on the existing userspace node `open5gs-vm1` (2026-11-26): `/oauth2/token`, `/device-identifier`,
  `/device-swap` published at `https://open5gs-vm1.tail5ec2cb.ts.net` for Guardian (guardian.dev). Tailnet policy got
  node attribute `funnel` for `tag:open5gs-vm1`. Undo: `tailscale funnel reset` (see README → Operations).

## Gotchas learned

- Open5GS SBI is **h2c only**: httpx needs `http1=False, http2=True`. The PCF calls back over h2c → Hypercorn, not uvicorn.
- NRF discovery as an AF hides UDM (`allowedNfTypes`) → health uses `nnrf-nfm` listings.
- The BSF binding has **no SUPI/GPSI** (only the PCF endpoint) → IP→SUPI comes from SMF `/pdu-info`.
- The PCF needs a PCC rule with the same 5QI on the subscriber, else 403. Flowless rules are skipped at PDU setup.
- Restarting the PCF drops SM policy contexts → app-session 404 until the UE re-establishes its PDU session.
- UERANSIM: the gNB drops N2 on a QoS-flow modify; it can't roam; after a gNB restart the UE can stay CM-IDLE and
  unreachable (restart `nr-ue`).
- VM-local traffic to `10.45.0.1` is MASQUERADEd (`-s 10.45.0.0/16 ! -o ogstun`) → a UE can't be faked from the VM;
  real UE traffic arrives on `ogstun` with its own IP.
- No passwordless sudo: the user restarts the service. For testing new code without a restart, run a second
  instance on :8091 with a scratch config (`listen.port` and `qod.callback_base` changed).
- Demo runs leave swap events → `lab_swap.py reset-history` before presenting.
- CAMARA test definitions sometimes differ from the OpenAPI error lists (e.g. QoD duration → `INVALID_ARGUMENT`);
  we follow the test definitions.

## Session log

- **2026-11-19:**
  - Research: no CAMARA-on-Open5GS project exists; free5GC NEF has no CAMARA code upstream → chose a sidecar on Open5GS.
  - Plan written (`PLAN.md`).
  - Phase 0: foundations, resolver, auth, health.
  - Phase 1: QoS Profiles + QoD; the PCF `qos_profiles` config was added.
- **2026-11-20:**
  - QoD verified live in the core (5QI 3 flow, expiry); found the UERANSIM N2 issue.
  - Phase 2: SIM/Device Swap + subscriptions.
  - Phase 3: Number Verification with network-based auth.
  - Phase 4: Roaming Status + subscriptions.
  - Phase 5: Connectivity Insights + Application Profiles.
  - Phase 6: run-all, Postman, conformance review (390 scenarios, about a dozen gaps fixed, rate limiting), Kuma script.
  - Architecture page published.
  - User gave the EQTY user stories → Device Identifier v0.4.0, story scripts, preflight, runbook; rehearsal fixes.
  - Branch pushed to the fork as a backup; no PR (local dev).
- **2026-11-21 … 11-26:**
  - Story 2: USA is the approved country, Germany is not. Branch pushed.
  - OAI gNB + nrUE (RF simulator) brought up on `192.168.64.6` by a separate session; nrUE deregistration ngKSI fixed
    there. It currently uses IMSI …001 (IMEISV `3528820000010101`, "Lab Field Unit"), so Device Swap for `+99970000001`
    reports `swapped: true` and the UERANSIM IMEI no longer matches.
  - Guardian gateway with a CAMARA plugin at `192.168.64.7`: per model call, token → Device Identifier
    `match-identifier` by IP and by phone. Demo shortcut: the UE IP comes from a header (VM1 NATs UE traffic).
  - Dedicated `guardian` client; Tailscale Funnel publishes only the paths Guardian uses (README → Operations).
