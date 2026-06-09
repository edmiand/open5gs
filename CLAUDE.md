# Open5GS Lab — Claude Code Context

## Deployment

Full step-by-step instructions for deploying this fork on a new arm64 Ubuntu 22.04 VM
are in [README.md](README.md#lab-deployment--edmiand-fork-arm64-ubuntu-2204).

## Target configuration

| Parameter | Value |
|-----------|-------|
| Fork | `https://github.com/edmiand/open5gs` |
| Install prefix | `~/open5gs/install` |
| PLMN | MCC `999`, MNC `70` |
| TAC | `1` |
| NSSAI SST | `1` |
| AMF NGAP / UPF GTP-U | VM's primary non-loopback IP (auto-detect) |
| TUN | `ogstun` — `10.45.0.1/16`, `2001:db8:cafe::1/48` |

## Key constraints

- All config edits non-interactive; only touch `install/etc/open5gs/*.yaml`.
- `sudo` only where required (packages, sysctl, iptables, systemd, tun setup).
- Fail loudly on any error — never silently continue.
- 5G Core only — skip 4G EPC NFs (MME, SGW-C/U, HSS, PCRF).
- WebUI systemd unit: enable but do **not** start (PORT=9999, HOSTNAME=0.0.0.0).

## Operations

See `open5gs-ctl.sh` for start/stop/status of NF processes.

NF startup order: NRF → SCP → AMF → SMF → UPF → AUSF → UDM → PCF → NSSF → BSF → UDR

Logs: `~/open5gs/install/var/log/open5gs/<nf>.log`
