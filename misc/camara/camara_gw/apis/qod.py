"""CAMARA Quality on Demand API (QualityOnDemand r4.3, quality-on-demand v1.2.0).

Southbound: the gateway acts as an AF towards the Open5GS PCF.

  create : resolve device -> BSF pcfBindings?ipv4Addr (PCF address) -> PCF app-session with
           qosReference=<profile>, marBw (MBR) / mirBw (GBR), flow descriptions -> QoS flow
  verify : SMF /pdu-info shows a new QoS flow with the profile's 5QI -> AVAILABLE
  expiry : PCF app-session delete -> UNAVAILABLE / DURATION_EXPIRED
  PCF    : POST {notifUri}/terminate -> UNAVAILABLE / NETWORK_TERMINATED

Sessions live in Mongo `camara_qod_sessions`. One active session per device (409 CONFLICT).
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .. import errors
from ..auth.tokens import Principal, require_scopes
from ..core.info import InfoUnavailable
from ..core.resolver import ResolvedDevice
from ..models import Device
from ..notify import validate_sink
from ..profiles import Profile, bitrate_str

log = logging.getLogger("camara.qod")

router = APIRouter(prefix="/quality-on-demand/v1", tags=["quality-on-demand"])
internal = APIRouter(prefix="/_internal/pcf/qod", tags=["internal"], include_in_schema=False)

EVENT_TYPE = "org.camaraproject.quality-on-demand.v1.qos-status-changed"
COLLECTION = "camara_qod_sessions"
ACTIVE = ("REQUESTED", "AVAILABLE")


# ── request models ───────────────────────────────────────────────────────────
class PortRange(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    from_: int = Field(..., alias="from", ge=0, le=65535)
    to: int = Field(..., ge=0, le=65535)

    @model_validator(mode="after")
    def _order(self):
        if self.from_ > self.to:
            raise ValueError("port range 'from' must be <= 'to'")
        return self


class PortsSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ranges: list[PortRange] | None = Field(None, min_length=1)
    ports: list[int] | None = Field(None, min_length=1)

    @model_validator(mode="after")
    def _non_empty(self):
        if not (self.ranges or self.ports):
            raise ValueError("PortsSpec needs ranges and/or ports")
        if self.ports and any(not 0 <= p <= 65535 for p in self.ports):
            raise ValueError("port out of range")
        return self

    def tokens(self) -> list[str]:
        return [str(p) for p in (self.ports or [])] + \
               [f"{r.from_}-{r.to}" if r.from_ != r.to else str(r.from_) for r in (self.ranges or [])]


class ApplicationServer(BaseModel):
    """oneOf {ipAddresses:[...]} | {ipv4Address?, ipv6Address?} (subnets)."""
    model_config = ConfigDict(extra="forbid")
    ipAddresses: list[str] | None = Field(None, min_length=1)
    ipv4Address: str | None = None
    ipv6Address: str | None = None

    @model_validator(mode="after")
    def _one_of(self):
        subnets = self.ipv4Address is not None or self.ipv6Address is not None
        if bool(self.ipAddresses) == subnets:
            raise ValueError("applicationServer needs either ipAddresses or ipv4Address/ipv6Address")
        try:
            for a in self.ipAddresses or []:
                ipaddress.ip_address(a)
            if self.ipv4Address:
                ipaddress.IPv4Network(self.ipv4Address, strict=False)
            if self.ipv6Address:
                ipaddress.IPv6Network(self.ipv6Address, strict=False)
        except ValueError as exc:
            raise ValueError(f"invalid applicationServer address: {exc}")
        return self

    def ipv4_targets(self) -> list[str]:
        if self.ipAddresses:
            return [a for a in self.ipAddresses if ipaddress.ip_address(a).version == 4]
        return [self.ipv4Address] if self.ipv4Address else []


class CreateSession(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device: Device | None = None
    applicationServer: ApplicationServer
    devicePorts: PortsSpec | None = None
    applicationServerPorts: PortsSpec | None = None
    qosProfile: str = Field(..., min_length=3, max_length=256, pattern=r"^[a-zA-Z0-9_.-]+$")
    sink: str | None = Field(None, max_length=2048)
    sinkCredential: dict | None = None
    duration: int = Field(..., ge=1, le=2147483647)


class ExtendSessionDuration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestedAdditionalDuration: int = Field(..., ge=1, le=2147483647)


class RetrieveSessionsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device: Device | None = None


# ── flow descriptions ────────────────────────────────────────────────────────
MAX_FLOWS_PER_SUB = 15      # OGS_MAX_NUM_OF_FLOW_IN_MEDIA_SUB_COMPONENT
MAX_SUB_COMPONENTS = 8      # OGS_MAX_NUM_OF_MEDIA_SUB_COMPONENT


def build_flow_descriptions(ue_ipv4: str, servers: list[str], as_ports: PortsSpec | None,
                            dev_ports: PortsSpec | None) -> list[str]:
    """IPFilterRule pairs (TS 29.214): 'permit out' = downlink AS->UE, 'permit in' = uplink UE->AS.

    With ports, explicit TCP (6) and UDP (17) rules are generated; without ports, 'ip'.
    """
    asp = as_ports.tokens() if as_ports else [None]
    dvp = dev_ports.tokens() if dev_ports else [None]
    protos = ["ip"] if as_ports is None and dev_ports is None else ["6", "17"]
    out: list[str] = []
    for srv in servers:
        for proto in protos:
            for ap in asp:
                for dp in dvp:
                    s_as = f"{srv} {ap}" if ap else srv
                    s_ue = f"{ue_ipv4} {dp}" if dp else ue_ipv4
                    out.append(f"permit out {proto} from {s_as} to {s_ue}")
                    out.append(f"permit in {proto} from {s_ue} to {s_as}")
    return out


def media_components(profile: Profile, fdescs: list[str]) -> dict:
    # keep downlink/uplink pairs together in a sub-component
    chunks = [fdescs[i:i + MAX_FLOWS_PER_SUB - 1] for i in range(0, len(fdescs), MAX_FLOWS_PER_SUB - 1)]
    if len(chunks) > MAX_SUB_COMPONENTS:
        raise errors.invalid_argument(
            f"Too many flows ({len(fdescs)}); reduce application server addresses or port specs.")
    return {"1": {
        "medCompN": 1,
        "qosReference": profile.name,
        "marBwDl": bitrate_str(profile.maxDownstreamRate),
        "marBwUl": bitrate_str(profile.maxUpstreamRate),
        "mirBwDl": bitrate_str(profile.targetMinDownstreamRate),
        "mirBwUl": bitrate_str(profile.targetMinUpstreamRate),
        "fStatus": "ENABLED",
        "medSubComps": {str(i + 1): {"fNum": i + 1, "fDescs": c} for i, c in enumerate(chunks)},
    }}


# ── service ──────────────────────────────────────────────────────────────────
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:                      # mongomock/pymongo without tz_aware
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


class QodService:
    def __init__(self, app_state):
        self.st = app_state
        self.col = app_state.store.db[COLLECTION]
        self._tasks: set[asyncio.Task] = set()
        self._loop_task: asyncio.Task | None = None

    # -- persistence helpers (sync pymongo in threads) --
    async def _find(self, q: dict) -> list[dict]:
        return await asyncio.to_thread(lambda: list(self.col.find(q)))

    async def _get(self, sid: str) -> dict | None:
        return await asyncio.to_thread(self.col.find_one, {"_id": sid})

    async def _set(self, sid: str, fields: dict) -> None:
        await asyncio.to_thread(self.col.update_one, {"_id": sid}, {"$set": fields})

    def _spawn(self, coro) -> None:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    # -- representation --
    @staticmethod
    def session_info(doc: dict) -> dict:
        out = {
            "sessionId": doc["_id"],
            "device": doc.get("device_response"),
            "applicationServer": doc["applicationServer"],
            "devicePorts": doc.get("devicePorts"),
            "applicationServerPorts": doc.get("applicationServerPorts"),
            "qosProfile": doc["qosProfile"],
            "sink": doc.get("sink"),
            "duration": doc["duration"],
            "startedAt": _iso(doc.get("startedAt")),
            "expiresAt": _iso(doc.get("expiresAt")),
            "qosStatus": doc["qosStatus"],
            "statusInfo": doc.get("statusInfo"),
        }
        return {k: v for k, v in out.items() if v is not None}

    def _notify(self, doc: dict, status: str, info: str | None = None) -> None:
        data = {"sessionId": doc["_id"], "qosStatus": status}
        if info:
            data["statusInfo"] = info
        self.st.notifier.send(doc.get("sink"), doc.get("sinkCredential"), EVENT_TYPE, data)

    async def _owned(self, sid: str, p: Principal) -> dict:
        doc = await self._get(sid)
        if not doc or doc.get("closed") or doc.get("client_id") != p.client_id:
            raise errors.CamaraError(404, "NOT_FOUND", "The specified session does not exist.")
        return doc

    # -- operations --
    async def create(self, body: CreateSession, p: Principal) -> dict:
        profile: Profile | None = self.st.profiles.get(body.qosProfile)
        if profile is None:
            raise errors.CamaraError(404, "NOT_FOUND", f"QoS profile {body.qosProfile} not found.")
        if profile.status != "ACTIVE":
            raise errors.CamaraError(422, "QUALITY_ON_DEMAND.QOS_PROFILE_NOT_APPLICABLE",
                                     f"QoS profile {profile.name} is {profile.status}.")
        if not profile.minDuration <= body.duration <= profile.maxDuration:
            raise errors.CamaraError(400, "QUALITY_ON_DEMAND.DURATION_OUT_OF_RANGE",
                                     f"duration must be between {profile.minDuration} and {profile.maxDuration} s "
                                     f"for {profile.name}.")
        validate_sink(self.st.config.notify, body.sink, body.sinkCredential)
        if body.device is not None and p.sub:
            raise errors.CamaraError(422, "UNNECESSARY_IDENTIFIER",
                                     "The device is already identified by the access token.")

        rd: ResolvedDevice = await self.st.resolver.resolve(body.device, require_session=True)
        sess = next((s for s in rd.sessions if s.ipv4), None)
        if sess is None:
            raise errors.service_not_applicable("Device has no IPv4 PDU session (IPv6-only QoD not supported).")
        servers = body.applicationServer.ipv4_targets()
        if not servers:
            raise errors.service_not_applicable("Only IPv4 application servers are supported.")

        if await self._find({"supi": rd.supi, "closed": {"$ne": True}, "qosStatus": {"$in": list(ACTIVE)}}):
            raise errors.CamaraError(409, "CONFLICT", "Conflict with an existing session for the same device.")

        fdescs = build_flow_descriptions(sess.ipv4, servers, body.applicationServerPorts, body.devicePorts)
        mcs = media_components(profile, fdescs)
        baseline_qfis = {f.get("qfi") for f in sess.qos_flows}

        # PCF address from the BSF binding (fallback: configured PCF)
        try:
            binding = await self.st.sbi.bsf_binding_by_ipv4(sess.ipv4)
        except httpx.HTTPError as exc:
            raise errors.unavailable(f"BSF unavailable: {exc}")
        if binding is None:
            raise errors.service_not_applicable("No policy binding for the device's PDU session.")
        eps = binding.get("pcfIpEndPoints") or []
        pcf_base = f"http://{eps[0]['ipv4Address']}:{eps[0].get('port', 80)}" if eps else self.st.config.sbi.pcf

        sid = str(uuid.uuid4())
        snssai = {k: v for k, v in (sess.snssai or {}).items() if k in ("sst", "sd") and v and v != "ffffff"}
        asc = {"ascReqData": {
            "afAppId": self.st.config.qod.af_app_id,
            "ueIpv4": sess.ipv4,
            "dnn": sess.dnn,
            "sliceInfo": snssai or None,
            "notifUri": f"{self.st.config.qod.callback_base}{internal.prefix}/{sid}",
            "suppFeat": "0",
            "medComponents": mcs,
        }}
        asc["ascReqData"] = {k: v for k, v in asc["ascReqData"].items() if v is not None}

        try:
            r = await self.st.sbi.pcf_app_session_create(pcf_base, asc)
        except httpx.HTTPError as exc:
            raise errors.unavailable(f"PCF unavailable: {exc}")
        if r.status_code == 404:
            raise errors.service_not_applicable(
                "PCF has no policy session for this device (stale binding?) - re-establish the PDU session.")
        if r.status_code == 403:
            detail = _problem_detail(r)
            raise errors.CamaraError(422, "QUALITY_ON_DEMAND.QOS_PROFILE_NOT_APPLICABLE",
                                     f"Network rejected profile {profile.name}: {detail}")
        if r.status_code != 201 or "location" not in r.headers:
            raise errors.unavailable(f"PCF app-session create failed: HTTP {r.status_code} {_problem_detail(r)}")

        doc = {
            "_id": sid, "client_id": p.client_id, "supi": rd.supi, "ue_ipv4": sess.ipv4, "psi": sess.psi,
            "device_response": rd.device_response if body.device is not None else None,
            "applicationServer": body.applicationServer.model_dump(exclude_none=True),
            "devicePorts": body.devicePorts.model_dump(by_alias=True, exclude_none=True) if body.devicePorts else None,
            "applicationServerPorts": body.applicationServerPorts.model_dump(by_alias=True, exclude_none=True)
            if body.applicationServerPorts else None,
            "qosProfile": profile.name, "fiveqi": profile.fiveqi, "duration": body.duration,
            "sink": body.sink, "sinkCredential": body.sinkCredential,
            "qosStatus": "REQUESTED", "createdAt": _now(),
            "pcf_app_session": r.headers["location"], "flow_descriptions": fdescs,
        }
        await asyncio.to_thread(self.col.insert_one, doc)
        log.info("QoD %s %s %s -> %s", sid, rd.supi, profile.name, doc["pcf_app_session"])
        self._spawn(self._verify(sid, rd.supi, profile.fiveqi, baseline_qfis))
        return self.session_info(doc)

    async def _verify(self, sid: str, supi: str, fiveqi: int, baseline: set) -> None:
        """Wait for the dedicated QoS flow in SMF /pdu-info; AVAILABLE or give up."""
        deadline = asyncio.get_running_loop().time() + self.st.config.qod.verify_timeout
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.5)
            doc = await self._get(sid)
            if not doc or doc.get("qosStatus") != "REQUESTED":
                return
            try:
                items = await self.st.info.pdu_info()
            except InfoUnavailable:
                continue
            flows = [f for it in items if it.get("supi") == supi for pdu in it.get("pdu", [])
                     for f in pdu.get("qos_flows", [])]
            new = [f for f in flows if f.get("5qi") == fiveqi and f.get("qfi") not in baseline]
            if new:
                start = _now()
                await self._set(sid, {"qosStatus": "AVAILABLE", "startedAt": start,
                                      "expiresAt": start + timedelta(seconds=doc["duration"]),
                                      "qfi": new[0].get("qfi")})
                log.info("QoD %s AVAILABLE (qfi %s, 5qi %s)", sid, new[0].get("qfi"), fiveqi)
                self._notify(doc, "AVAILABLE")
                return
        doc = await self._get(sid)
        if doc and doc.get("qosStatus") == "REQUESTED":
            log.warning("QoD %s: no QoS flow with 5QI %s within %.0fs", sid, fiveqi, self.st.config.qod.verify_timeout)
            await self._close(doc, "NETWORK_TERMINATED", release=True)

    async def _close(self, doc: dict, reason: str, *, release: bool) -> None:
        if release and doc.get("pcf_app_session"):
            try:
                r = await self.st.sbi.pcf_app_session_delete(doc["pcf_app_session"])
                if r.status_code not in (200, 204, 404):
                    log.warning("QoD %s: PCF delete HTTP %d", doc["_id"], r.status_code)
            except httpx.HTTPError as exc:
                log.warning("QoD %s: PCF delete failed: %s", doc["_id"], exc)
        was = doc.get("qosStatus")
        await self._set(doc["_id"], {"qosStatus": "UNAVAILABLE", "statusInfo": reason, "closed": True,
                                     "closedAt": _now()})
        if was in ACTIVE:
            self._notify(doc, "UNAVAILABLE", reason)

    async def get(self, sid: str, p: Principal) -> dict:
        return self.session_info(await self._owned(sid, p))

    async def delete(self, sid: str, p: Principal) -> None:
        doc = await self._owned(sid, p)
        await self._close(doc, "DELETE_REQUESTED", release=True)

    async def extend(self, sid: str, body: ExtendSessionDuration, p: Principal) -> dict:
        doc = await self._owned(sid, p)
        if doc["qosStatus"] != "AVAILABLE":
            raise errors.CamaraError(409, "QUALITY_ON_DEMAND.SESSION_EXTENSION_NOT_ALLOWED",
                                     "Extending the session duration is not allowed in the current state "
                                     f"({doc['qosStatus']}). The session must be AVAILABLE.")
        profile = self.st.profiles[doc["qosProfile"]]
        new_duration = min(doc["duration"] + body.requestedAdditionalDuration, profile.maxDuration)
        expires = doc["startedAt"] + timedelta(seconds=new_duration)
        await self._set(sid, {"duration": new_duration, "expiresAt": expires})
        return self.session_info(await self._get(sid))

    async def retrieve(self, body: RetrieveSessionsInput, p: Principal) -> list[dict]:
        if body.device is not None and p.sub:
            raise errors.CamaraError(422, "UNNECESSARY_IDENTIFIER",
                                     "The device is already identified by the access token.")
        rd = await self.st.resolver.resolve(body.device)
        docs = await self._find({"supi": rd.supi, "client_id": p.client_id, "closed": {"$ne": True}})
        out = []
        for d in docs:
            info = self.session_info(d)
            if body.device is not None:
                info["device"] = rd.device_response
            out.append(info)
        return out

    async def on_pcf_terminate(self, sid: str, payload: dict) -> None:
        doc = await self._get(sid)
        if not doc or doc.get("closed"):
            return
        log.info("QoD %s: PCF terminate (%s)", sid, payload.get("termCause"))
        # PCF expects the AF to delete the app-session after a termination request
        await self._close(doc, "NETWORK_TERMINATED", release=True)

    # -- expiry loop --
    async def run_expiry(self) -> None:
        while True:
            try:
                for doc in await self._find({"qosStatus": "AVAILABLE", "closed": {"$ne": True},
                                             "expiresAt": {"$lte": _now()}}):
                    log.info("QoD %s expired", doc["_id"])
                    await self._close(doc, "DURATION_EXPIRED", release=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("QoD expiry loop error")
            await asyncio.sleep(self.st.config.qod.expiry_interval)

    def start(self) -> None:
        self._loop_task = asyncio.create_task(self.run_expiry())

    async def stop(self) -> None:
        for t in [self._loop_task, *self._tasks]:
            if t:
                t.cancel()


def _problem_detail(r: httpx.Response) -> str:
    try:
        j = r.json()
        return j.get("detail") or j.get("title") or r.text[:200]
    except ValueError:
        return r.text[:200]


# ── routes ───────────────────────────────────────────────────────────────────
def _svc(request: Request) -> QodService:
    return request.app.state.qod


@router.post("/sessions", status_code=201)
async def create_session(body: CreateSession, request: Request,
                         p: Principal = Depends(require_scopes("quality-on-demand:sessions:create"))):
    info = await _svc(request).create(body, p)
    return _with_location(request, info)


def _with_location(request: Request, info: dict):
    from fastapi.responses import JSONResponse
    loc = str(request.url_for("get_session", sessionId=info["sessionId"]))
    return JSONResponse(info, status_code=201, headers={"location": loc})


@router.get("/sessions/{sessionId}", name="get_session")
async def get_session(sessionId: str, request: Request,
                      p: Principal = Depends(require_scopes("quality-on-demand:sessions:read"))):
    return await _svc(request).get(sessionId, p)


@router.delete("/sessions/{sessionId}", status_code=204)
async def delete_session(sessionId: str, request: Request,
                         p: Principal = Depends(require_scopes("quality-on-demand:sessions:delete"))):
    await _svc(request).delete(sessionId, p)
    return Response(status_code=204)


@router.post("/sessions/{sessionId}/extend")
async def extend_session(sessionId: str, body: ExtendSessionDuration, request: Request,
                         p: Principal = Depends(require_scopes("quality-on-demand:sessions:update"))):
    return await _svc(request).extend(sessionId, body, p)


@router.post("/retrieve-sessions")
async def retrieve_sessions(body: RetrieveSessionsInput, request: Request,
                            p: Principal = Depends(require_scopes("quality-on-demand:sessions:retrieve-by-device"))):
    return await _svc(request).retrieve(body, p)


# PCF -> gateway (Npcf_PolicyAuthorization notifications). Loopback-only.
def _loopback_only(request: Request) -> None:
    host = request.client.host if request.client else ""
    try:
        ok = ipaddress.ip_address(host).is_loopback
    except ValueError:
        ok = False
    if not ok:
        raise errors.permission_denied("Internal callback endpoint.")


@internal.post("/{sessionId}/terminate", status_code=204, dependencies=[Depends(_loopback_only)])
async def pcf_terminate(sessionId: str, request: Request):
    try:
        payload = await request.json()
    except ValueError:
        payload = {}
    _svc(request)._spawn(_svc(request).on_pcf_terminate(sessionId, payload))
    return Response(status_code=204)


@internal.post("/{sessionId}/notify", status_code=204, dependencies=[Depends(_loopback_only)])
async def pcf_event_notify(sessionId: str, request: Request):
    log.info("QoD %s: PCF event notification %s", sessionId, (await request.body())[:300])
    return Response(status_code=204)
