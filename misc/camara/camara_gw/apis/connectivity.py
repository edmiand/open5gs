"""CAMARA Application Profiles (ApplicationProfiles r2.3, v0.6.0) and
Connectivity Insights (ConnectivityInsights r4.3, v0.7.0) check-network-quality.

Profiles: CRUD in Mongo `camara_app_profiles`, owned by the creating client. PATCH replaces the
whole threshold set (per spec). computeResources are stored but not evaluated (no edge compute here).

check-network-quality: resolve device -> UE IP -> probe (ICMP; iperf3 optional) -> compare each
network threshold -> "meets the application requirements" / "unable to meet the application
requirements". KPIs that can't be measured (throughput without iperf3) are omitted, not guessed.
monitoringTimeStamp is accepted but measurements are always current (no prediction in the lab).
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .. import errors
from ..auth.tokens import Principal, require_scopes
from ..models import Device
from .qod import PortsSpec

log = logging.getLogger("camara.ci")

profiles_router = APIRouter(prefix="/application-profiles/v0.6", tags=["application-profiles"])
ci_router = APIRouter(prefix="/connectivity-insights/v0.7", tags=["connectivity-insights"])
lab_router = APIRouter(prefix="/_lab", tags=["lab"])
COLLECTION = "camara_app_profiles"
MEETS = "meets the application requirements"
UNABLE = "unable to meet the application requirements"

_TIME_MS = {"Days": 86_400_000, "Hours": 3_600_000, "Minutes": 60_000, "Seconds": 1000, "Milliseconds": 1,
            "Microseconds": 1e-3, "Nanoseconds": 1e-6}
_RATE_BPS = {"bps": 1, "kbps": 1e3, "Mbps": 1e6, "Gbps": 1e9, "Tbps": 1e12}


# ── schemas ──────────────────────────────────────────────────────────────────
class Duration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int = Field(..., ge=1, le=999)
    unit: str = Field(..., pattern="^(Days|Hours|Minutes|Seconds|Milliseconds|Microseconds|Nanoseconds)$")

    def ms(self) -> float:
        return self.value * _TIME_MS[self.unit]


class Rate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int = Field(..., ge=0, le=1024)
    unit: str = Field(..., pattern="^(bps|kbps|Mbps|Gbps|Tbps)$")

    def bps(self) -> float:
        return self.value * _RATE_BPS[self.unit]


class NetworkQualityThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    packetDelayBudget: Duration | None = None
    targetMinDownstreamRate: Rate | None = None
    targetMinUpstreamRate: Rate | None = None
    packetLossErrorRate: int | None = Field(None, ge=1, le=10)
    jitter: Duration | None = None


class ApplicationProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    networkQualityThresholds: NetworkQualityThresholds | None = None
    computeResources: dict | None = None

    def validate_non_empty(self) -> None:
        nqt = self.networkQualityThresholds
        if (nqt is None or not nqt.model_dump(exclude_none=True)) and not self.computeResources:
            raise errors.invalid_argument("At least one network quality or compute threshold is required.")


class ApplicationServer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ipv4Address: str | None = None
    ipv6Address: str | None = None

    @model_validator(mode="after")
    def _valid(self):
        if not (self.ipv4Address or self.ipv6Address):
            raise ValueError("applicationServer needs ipv4Address and/or ipv6Address")
        try:
            if self.ipv4Address:
                ipaddress.IPv4Network(self.ipv4Address, strict=False)
            if self.ipv6Address:
                ipaddress.IPv6Network(self.ipv6Address, strict=False)
        except ValueError as exc:
            raise ValueError(f"invalid applicationServer address: {exc}")
        return self


class NetworkQualityInsightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    applicationProfileId: str = Field(..., max_length=256)
    device: Device | None = None
    applicationServer: ApplicationServer
    applicationServerPorts: PortsSpec | None = None
    monitoringTimeStamp: datetime | None = None


# ── application profiles ─────────────────────────────────────────────────────
def _render(doc: dict) -> dict:
    out = {"applicationProfileId": doc["_id"]}
    for k in ("networkQualityThresholds", "computeResources"):
        if doc.get(k):
            out[k] = doc[k]
    return out


async def _owned(request: Request, pid: str, p: Principal) -> dict:
    col = request.app.state.store.db[COLLECTION]
    doc = await asyncio.to_thread(col.find_one, {"_id": pid, "client_id": p.client_id})
    if not doc:
        raise errors.CamaraError(404, "NOT_FOUND", "The specified application profile does not exist.")
    return doc


def _fields(body: ApplicationProfileRequest) -> dict:
    nqt = body.networkQualityThresholds.model_dump(exclude_none=True) if body.networkQualityThresholds else None
    return {"networkQualityThresholds": nqt or None, "computeResources": body.computeResources or None}


@profiles_router.post("/application-profiles", status_code=201)
async def create_profile(body: ApplicationProfileRequest, request: Request,
                         p: Principal = Depends(require_scopes("application-profiles:create"))):
    body.validate_non_empty()
    doc = {"_id": str(uuid.uuid4()), "client_id": p.client_id, **_fields(body),
           "created": datetime.now(timezone.utc)}
    await asyncio.to_thread(request.app.state.store.db[COLLECTION].insert_one, doc)
    return JSONResponse(_render(doc), status_code=201)


@profiles_router.get("/application-profiles/{applicationProfileId}")
async def get_profile(applicationProfileId: str, request: Request,
                      p: Principal = Depends(require_scopes("application-profiles:read"))):
    return _render(await _owned(request, applicationProfileId, p))


@profiles_router.patch("/application-profiles/{applicationProfileId}")
async def replace_profile(applicationProfileId: str, body: ApplicationProfileRequest, request: Request,
                          p: Principal = Depends(require_scopes("application-profiles:update"))):
    await _owned(request, applicationProfileId, p)
    body.validate_non_empty()
    col = request.app.state.store.db[COLLECTION]
    await asyncio.to_thread(col.update_one, {"_id": applicationProfileId}, {"$set": _fields(body)})
    return _render(await _owned(request, applicationProfileId, p))


@profiles_router.delete("/application-profiles/{applicationProfileId}", status_code=204)
async def delete_profile(applicationProfileId: str, request: Request,
                         p: Principal = Depends(require_scopes("application-profiles:delete"))):
    await _owned(request, applicationProfileId, p)
    await asyncio.to_thread(request.app.state.store.db[COLLECTION].delete_one, {"_id": applicationProfileId})
    return Response(status_code=204)


# ── connectivity insights ────────────────────────────────────────────────────
def assess(nqt: dict, ping, down_bps: float | None, up_bps: float | None) -> tuple[dict, dict]:
    """Return (CAMARA response fields, measured values) for the thresholds in `nqt`."""
    res, meas = {}, {"oneWayDelayMs": ping.one_way_ms, "jitterMs": ping.jitter_ms, "lossRate": ping.loss_rate,
                     "pingSent": ping.sent, "pingReceived": ping.received,
                     "downstreamBps": down_bps, "upstreamBps": up_bps}
    unreachable = ping.received == 0
    if "packetDelayBudget" in nqt:
        ok = not unreachable and ping.one_way_ms <= Duration(**nqt["packetDelayBudget"]).ms()
        res["packetDelayBudget"] = MEETS if ok else UNABLE
    if "jitter" in nqt:
        j = ping.jitter_ms
        ok = not unreachable and j is not None and j <= Duration(**nqt["jitter"]).ms()
        res["jitter"] = MEETS if ok else UNABLE
    if "packetLossErrorRate" in nqt:
        # measured loss must not exceed 10^-N; resolution is 1/pingSent (zero loss is the best we can show)
        allowed = 10 ** (-int(nqt["packetLossErrorRate"]))
        ok = not unreachable and ping.loss_rate <= allowed
        res["packetlossErrorRate"] = MEETS if ok else UNABLE        # (sic) response field name per spec
    for key, val in (("targetMinDownstreamRate", down_bps), ("targetMinUpstreamRate", up_bps)):
        if key in nqt and val is not None:
            res[key] = MEETS if val >= Rate(**nqt[key]).bps() else UNABLE
        elif key in nqt and unreachable:
            res[key] = UNABLE
    return res, meas


@ci_router.post("/check-network-quality")
async def check_network_quality(body: NetworkQualityInsightRequest, request: Request,
                                p: Principal = Depends(require_scopes("connectivity-insights:check"))):
    st = request.app.state
    if body.device is not None and p.sub:
        raise errors.CamaraError(422, "UNNECESSARY_IDENTIFIER", "The device is already identified by the access token.")
    col = st.store.db[COLLECTION]
    prof = await asyncio.to_thread(col.find_one, {"_id": body.applicationProfileId, "client_id": p.client_id})
    if not prof:
        raise errors.CamaraError(404, "NOT_FOUND", "The specified application profile does not exist.")
    nqt = prof.get("networkQualityThresholds") or {}
    if not nqt:
        raise errors.invalid_argument("The application profile has no network quality thresholds.")
    rd = await st.resolver.resolve(body.device, require_session=True)
    ue_ip = next((s.ipv4 for s in rd.sessions if s.ipv4), None)
    if ue_ip is None:
        raise errors.service_not_applicable("Device has no IPv4 PDU session.")

    want_tp = "targetMinDownstreamRate" in nqt or "targetMinUpstreamRate" in nqt
    ping = await st.prober.ping(ue_ip)
    down, up = (await st.prober.throughput_bps(ue_ip)) if want_tp else (None, None)
    res, meas = assess(nqt, ping, down, up)
    res["additionalKPIs"] = {"connectivityType": "5G"}
    log.info("CI %s profile=%s ue=%s measured=%s result=%s", rd.supi, prof["_id"], ue_ip,
             {k: (round(v, 3) if isinstance(v, float) else v) for k, v in meas.items()},
             {k: ("meets" if v == MEETS else "unable") for k, v in res.items() if k != "additionalKPIs"})
    st.last_ci = {"time": datetime.now(timezone.utc).isoformat(), "supi": rd.supi, "ueIpv4": ue_ip,
                  "applicationProfileId": prof["_id"], "thresholds": nqt, "measured": meas,
                  "result": {k: v for k, v in res.items() if k != "additionalKPIs"}}
    if body.device is not None:
        res = {"device": rd.device_response, **res}
    return res


@lab_router.get("/connectivity-insights/last")
async def last_measurement(request: Request, _: Principal = Depends(require_scopes("lab:read"))):
    """Raw measurements behind the last check-network-quality verdict (lab demo aid)."""
    last = getattr(request.app.state, "last_ci", None)
    if last is None:
        raise errors.CamaraError(404, "NOT_FOUND", "No measurement yet.")
    return last
