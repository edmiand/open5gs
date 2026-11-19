# open5gs-camara — CAMARA API gateway for the Open5GS lab

A sidecar that exposes [CAMARA](https://camaraproject.org) network APIs on top of this
Open5GS 5GC lab. It runs as an AF / "mini-NEF": the APIs are served northbound, and it
calls the core southbound (SBI h2c, the SMF/AMF info endpoints, MongoDB).
Design, scope and phases: [PLAN.md](PLAN.md).

**Status:** Phase 0 (foundations) — health, auth, CAMARA error model, device resolver.
No CAMARA API endpoints yet.

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
.venv/bin/python -m camara_gw            # foreground, listens on 0.0.0.0:8090
sudo systemctl status open5gs-camara     # if installed as a service
curl -s localhost:8090/health | jq .     # 200 UP / 503 DOWN with per-dependency checks
```

## Endpoints (so far)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/health` | none | Mongo, NRF registration of BSF/PCF/UDM, SMF `/pdu-info`, AMF `/ue-info` |
| POST | `/oauth2/token` | client credentials (Basic or form) | Issues HS256 JWT, `grant_type=client_credentials` |
| POST | `/_lab/resolve` | Bearer, scope `lab:read` | Resolve a CAMARA `device` → SUPI, phone, UE IPs (lab only, `lab_endpoints: true`) |

All errors use the CAMARA `ErrorInfo` shape `{status, code, message}`. `x-correlator` is
validated and echoed back (generated if absent).

## Demo

```bash
./demo/00-foundations.sh                              # health, token, resolve by phone
PHONE=+99970000001 UE_IP=10.45.0.2 ./demo/00-foundations.sh
```

For a successful resolve, the demo subscriber needs an MSISDN (WebUI → subscriber → MSISDN), and
for IP resolution the UE needs an active PDU session.

## Tests

```bash
.venv/bin/python -m pytest            # unit tests, core mocked (MockTransport + mongomock)
.venv/bin/python -m pytest -m live    # live smoke against the running lab (later phases)
```

## Implementation notes

- **SBI is h2c-only.** httpx must use `http1=False, http2=True` (prior knowledge). Plain
  `http2=True` sends HTTP/1.1 over cleartext, and the NFs drop it.
- **Don't use NRF discovery for health/lookup as an AF.** Open5GS filters `nnrf-disc` results by the
  target's `allowedNfTypes`, so UDM is invisible to an AF/NEF requester. The gateway uses
  `nnrf-nfm` listings and fixed NF addresses from `config.yaml`.
- `/pdu-info` and `/ue-info` are Open5GS-specific (metrics port, HTTP/1.1). All knowledge of their
  shape lives in `camara_gw/core/info.py`.
- The gateway never writes Open5GS collections. Its own state goes in `camara_*` collections.
- Helpers in `core/mongo.py` and `core/info.py` are adapted from `~/open5gs-mcp` (provenance noted
  in each file).
