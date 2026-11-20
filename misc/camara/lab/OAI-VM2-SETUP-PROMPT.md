# Prompt: set up OAI gNB + nrUE (RF simulator) on VM2

Paste everything below the line into a Claude Code session running **on VM2**.

Before that, run the VM1 prep below (it gives the OAI UE its own SIM, separate from UERANSIM's).

**VM1 prep** (core VM, `~/open5gs/misc/camara`):
```bash
.venv/bin/python tools/lab_swap.py move-msisdn 99970000002 --to 999700000000002   # MSISDN for the OAI UE's SIM
.venv/bin/python tools/seed_pcc_rules.py --imsi 999700000000002                    # PCC templates so QoD works for it
.venv/bin/python tools/lab_swap.py show
```

---

You are setting up a **software-only 5G RAN on this VM (VM2)**: OpenAirInterface (OAI) gNB + OAI nrUE running in
**RF simulator** mode (no radio hardware). It attaches to an existing **Open5GS 5G core on VM1**. The purpose is to test
a CAMARA **Quality on Demand** flow: the core adds a dedicated QoS flow to a live PDU session (NGAP
`PDUSessionResourceModifyRequest` + NAS PDU Session Modification Command). The UERANSIM gNB used so far doesn't support
that and drops N2, so the RAN must handle it and keep the UE connected.

## Source
- Repo: https://github.com/duranta-project/openairinterface5g, branch `develop`. This is the primary repo;
  `github.com/openairinterface/openairinterface5g` is a read-only mirror of it.
- Relevant evidence (verify against the current tree; don't assume):
  - The gNB decodes `PDUSessionResourceModifyRequest` with QosFlowAddOrModify/Release lists
    (`openair3/NGAP/ngap_gNB_handlers.c` → `rrc_gNB_process_NGAP_PDUSESSION_MODIFY_REQ` in `openair2/RRC/NR/rrc_gNB.c`).
  - nrUE PDU Session Modification + UL QoS-flow matching: PR #432, merged 2026-10-09.
  - Still open: PR #831 (E1AP DRBs on modify; only affects split CU-CP/CU-UP, so run a **monolithic** gNB) and
    PR #1131 (nrUE initial QoS rules).
- Follow the repo's own current docs for build and run flags, which change between versions:
  `doc/NR_SA_Tutorial_OAI_nrUE.md`, `doc/RUNMODEM.md`, `radio/rfsimulator/README.md`, `doc/BUILD.md`. Prefer them
  over memory.

## Core network facts (VM1 — do not modify VM1)
| Item | Value |
|---|---|
| AMF N2 (NGAP, SCTP) | `192.168.64.2:38412` |
| UPF N3 (GTP-U, UDP) | `192.168.64.2:2152` |
| PLMN | MCC `999`, MNC `70` (2-digit MNC) |
| TAC | `1` |
| S-NSSAI | SST `1`, **no SD** |
| DNN | `internet` (IPv4v6; UE pool `10.45.0.0/16`, gateway `10.45.0.1`) |
| NAS security offered | integrity NIA2/NIA1/NIA0, ciphering NEA0/NEA1/NEA2 |
| CAMARA gateway (as seen from the UE) | `http://10.45.0.1:8090` |

**UE subscriber to use (already provisioned on VM1):**

| Item | Value |
|---|---|
| IMSI | `999700000000002` |
| MSISDN | `+99970000002` |
| K | `465B5CE8B199B49FAA5F0A2EE238A6BC` |
| OPc | `E8ED289DEBA952E4283B54E88E6183CA` |
| AMF field | `8000` |

These are the well-known Open5GS/UERANSIM lab sample values. **Do not use IMSI `999700000000001`**: the UERANSIM UE
uses it.

## Constraints
- Work only on VM2. Use `sudo` only where required (packages, running the softmodems, network config). Fail loudly;
  never silently continue after an error.
- Don't modify OAI source code unless a build fix is unavoidable; if so, stop and report first.
- Record the exact OAI commit hash used.
- **CPU architecture:** check `uname -m` first. The lab VMs are likely **aarch64**. Confirm from the OAI docs and build
  output that the gNB + nrUE build and run on this architecture. If the build fails on aarch64, stop and report the error.
  Don't work around it with emulation.
- **Resources:** RF simulator gNB + nrUE on one VM need roughly ≥ 4 vCPU and ≥ 8 GB RAM. Report what this VM has; if
  below that, say so before building. Prefer the lightest documented RF simulator configuration (smallest PRB count
  that the docs support for SA + rfsim).
- If **UERANSIM's nr-gnb runs on this VM**, it already binds GTP-U `:2152` on the host IP and will clash with the OAI
  gNB. Stop nr-gnb (and nr-ue) while OAI runs, and say so; don't uninstall UERANSIM.

## Tasks
1. **Pre-flight:** report OS, arch, vCPU, RAM, free disk (build needs several GB), this VM's IP on `192.168.64.0/24`,
   reachability of `192.168.64.2` (ping), and whether anything listens on UDP 2152.
2. **Build:** clone the repo (`develop`), install dependencies, and build the gNB and nrUE for RF simulator per
   `doc/BUILD.md` (e.g. `build_oai -I` then the SIMU/gNB/nrUE build). Report the commit hash and build result.
3. **Configure the gNB** from the closest documented SA + rfsim config:
   - PLMN 999/70 (mnc_length 2), TAC 1, SST 1 with no SD;
   - AMF IPv4 `192.168.64.2`;
   - NG-AMF and NG-U local addresses = **this VM's IP**;
   - monolithic gNB (not CU/DU split).
   Keep the file under `~/oai-lab/conf/`.
4. **Configure the nrUE:** IMSI, K, OPc as above; DNN `internet`; NSSAI SST 1; RF simulator pointing at the local gNB.
   Keep it under `~/oai-lab/conf/`.
5. **Run scripts:** `~/oai-lab/run-gnb.sh`, `~/oai-lab/run-ue.sh`, `~/oai-lab/stop.sh`, logging to `~/oai-lab/logs/`.
   Optional: systemd units, enabled but not started.
6. **Attach test:**
   - Start the gNB, then the UE.
   - Verify NG Setup succeeded and the UE registered and got a PDU session: the UE tun interface (OAI names it like
     `oaitun_ue1`) has an address in `10.45.0.0/16`.
   - Verify `ping -I <ue-tun> 10.45.0.1` works, and `curl --interface <ue-tun> http://10.45.0.1:8090/health` returns
     `"status":"UP"`.
7. **QoD acceptance test** (coordinate with the VM1 operator):
   - On VM1, run `PHONE=+99970000002 ./misc/camara/demo/story3-quality.sh`.
   - Confirm in the gNB/UE logs that the PDU Session Resource Modify / PDU Session Modification Command was accepted.
   - Confirm a new QoS flow / DRB was added (VM1 sees an extra QFI with 5QI 4 for `imsi-999700000000002` in SMF
     `/pdu-info`) and that the **UE stays registered and the data path keeps working** afterwards (ping again).
   - After the session is deleted, the extra flow should be released without dropping the UE.
   - Capture the relevant log excerpts.
8. **Write `~/oai-lab/README.md`:**
   - OAI commit, build flags, config diffs from the stock files, and how to start/stop;
   - the UE tun interface name — the CAMARA demo scripts use `--interface uesimtun0` for UERANSIM, so state the OAI
     equivalent;
   - known issues found.

## Report back
- Pass/fail per task.
- Commit hash, resource figures, the interface name and UE IP.
- The QoD log evidence.
- Anything that needs a decision. Possible ones: aarch64 build problems, too little CPU for rfsim, UL traffic not
  using the new QFI (a known rfsim bug was reported upstream).
