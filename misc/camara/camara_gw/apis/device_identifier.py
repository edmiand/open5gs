"""CAMARA Device Identifier (DeviceIdentifier r3.5, device-identifier v0.4.0).

  POST /retrieve-identifier -> {lastChecked, imei, imeisv, tac, model?, manufacturer?}
  POST /retrieve-type       -> {lastChecked, tac, model?, manufacturer?}
  POST /retrieve-ppid       -> {lastChecked, ppid}
  POST /match-identifier    {imei | imeisv | tac} -> {lastChecked, match}

The device is whatever the network currently associates with the subscription:
1. AMF /ue-info `pei` (imeisv-...) for a UE known to the AMF -> lastChecked = AMF location timestamp
2. otherwise the IMEISV the UDR last wrote to `subscribers` -> lastChecked = null (time unknown)
No device ever seen -> 422 SERVICE_NOT_APPLICABLE.

IMEI (15 digits) = TAC (8) + serial (6) + Luhn check digit; IMEISV (16) = TAC + serial + SVN (2).
PPID = HMAC-SHA256(gateway key, client_id || IMEI): stable per (API consumer, device), unlinkable
across consumers. Model/manufacturer come from camara_gw/tac.yaml (lab table) when known.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from datetime import datetime, timezone
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .. import errors
from ..auth.tokens import Principal, require_scopes
from ..core.info import InfoUnavailable
from ..models import Device

router = APIRouter(prefix="/device-identifier/v0.4", tags=["device-identifier"])
TAC_FILE = Path(__file__).resolve().parent.parent / "tac.yaml"


def luhn_check_digit(digits14: str) -> str:
    total = 0
    for i, ch in enumerate(digits14):
        d = int(ch)
        if i % 2 == 1:          # double every second digit (from the left, 0-based odd positions)
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((10 - total % 10) % 10)


def split_imeisv(raw) -> dict | None:
    """'imeisv-4370816125816151' | '4370816125816151' | 15-digit IMEI -> {imei, imeisv?, tac}."""
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    if not raw or not isinstance(raw, str):
        return None
    d = raw.split("-", 1)[1] if "-" in raw else raw
    if not d.isdigit() or len(d) not in (14, 15, 16):
        return None
    base = d[:14]
    out = {"imei": base + luhn_check_digit(base), "tac": base[:8]}
    if len(d) == 16:
        out["imeisv"] = d
    return out


def _tac_table() -> dict:
    try:
        return {str(k): v for k, v in (yaml.safe_load(TAC_FILE.read_text()) or {}).items()}
    except FileNotFoundError:
        return {}


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z") if dt else None


class IdentifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device: Device | None = None


class MatchRequest(IdentifyRequest):
    imeisv: str | None = Field(None, pattern=r"^[0-9]{16}$")
    imei: str | None = Field(None, pattern=r"^[0-9]{15}$")
    tac: str | None = Field(None, pattern=r"^[0-9]{8}$")

    @model_validator(mode="after")
    def _one_of(self):
        if sum(x is not None for x in (self.imeisv, self.imei, self.tac)) != 1:
            raise ValueError("exactly one of imeisv, imei or tac is required")
        return self


async def _current_device(request: Request, body: IdentifyRequest, p: Principal) -> tuple[dict, dict, dict | None]:
    """-> (device identifiers, extra response fields {device?, lastChecked}, raw)."""
    st = request.app.state
    rd = await st.resolver.resolve_for(body.device, p)
    ids, last = None, None
    try:
        for it in await st.info.ue_info():
            if it.get("supi") == rd.supi:
                ids = split_imeisv(it.get("pei"))
                ts = (it.get("location") or {}).get("timestamp")
                last = datetime.fromtimestamp(ts / 1e6, timezone.utc) if ts else datetime.now(timezone.utc)
                break
    except InfoUnavailable:
        pass
    if ids is None:                                   # not attached: last IMEISV stored by the UDR
        doc = await asyncio.to_thread(st.store.subscriber_by_imsi, rd.imsi)
        ids = split_imeisv((doc or {}).get("imeisv"))
        last = None
    if ids is None:
        raise errors.service_not_applicable("No device information is available for this subscription.")
    extra = {"lastChecked": _iso(last)}
    if body.device is not None:
        extra = {"device": rd.device_response, **extra}
    return ids, extra, rd


def _type_fields(ids: dict) -> dict:
    out = {"tac": ids["tac"]}
    info = _tac_table().get(ids["tac"]) or {}
    for k in ("manufacturer", "model"):
        if info.get(k):
            out[k] = str(info[k])[:32]
    return out


@router.post("/retrieve-identifier")
async def retrieve_identifier(body: IdentifyRequest, request: Request,
                              p: Principal = Depends(require_scopes("device-identifier:retrieve-identifier"))):
    ids, extra, _ = await _current_device(request, body, p)
    out = {**extra, "imei": ids["imei"], **({"imeisv": ids["imeisv"]} if "imeisv" in ids else {}), **_type_fields(ids)}
    return out


@router.post("/retrieve-type")
async def retrieve_type(body: IdentifyRequest, request: Request,
                        p: Principal = Depends(require_scopes("device-identifier:retrieve-type"))):
    ids, extra, _ = await _current_device(request, body, p)
    return {**extra, **_type_fields(ids)}


@router.post("/retrieve-ppid")
async def retrieve_ppid(body: IdentifyRequest, request: Request,
                        p: Principal = Depends(require_scopes("device-identifier:retrieve-ppid"))):
    ids, extra, _ = await _current_device(request, body, p)
    key = request.app.state.config.auth.signing_key.encode()
    ppid = hmac.new(key, f"{p.client_id}|{ids['imei']}".encode(), hashlib.sha256).hexdigest()
    return {**extra, "ppid": ppid}


@router.post("/match-identifier")
async def match_identifier(body: MatchRequest, request: Request,
                           p: Principal = Depends(require_scopes("device-identifier:match-identifier"))):
    ids, extra, _ = await _current_device(request, body, p)
    if body.imeisv is not None:
        match = ids.get("imeisv") == body.imeisv
    elif body.imei is not None:
        match = ids["imei"] == body.imei
    else:
        match = ids["tac"] == body.tac
    return {**extra, "match": match}
