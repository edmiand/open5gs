"""CAMARA SIM Swap (SimSwap r4.3, sim-swap v2.2.0) and Device Swap (DeviceSwap r4.3, v1.1.0).

Both: POST /retrieve-date {phoneNumber} and POST /check {phoneNumber, maxAge (hours, default 240)}.
History comes from camara_gw.swap_history (detection-time events since monitoring started).

When no swap has been seen since monitoring started, retrieve-date returns
latestSimChange/latestDeviceChange = null plus monitoredPeriod (days monitored), as the spec
allows, and check returns swapped=false.
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from .. import errors
from ..auth.tokens import Principal, require_any_scope
from ..core.mongo import msisdn_from_e164
from ..models import PHONE_PATTERN


class DateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phoneNumber: str | None = Field(None, pattern=PHONE_PATTERN)


class CheckRequest(DateRequest):
    maxAge: int = Field(240, ge=1)          # > 2400 or > supported period -> 400 OUT_OF_RANGE (route)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


async def _phone(request: Request, body: DateRequest, p: Principal) -> str:
    """Validate the subject and return the MSISDN (digits)."""
    if body.phoneNumber and p.sub:
        raise errors.CamaraError(422, "UNNECESSARY_IDENTIFIER", "The phone number is already identified by the access token.")
    if p.sub:
        phone = await request.app.state.resolver.phone_for_supi(p.sub)
        if phone is None:
            raise errors.service_not_applicable("No phone number is allocated to the device.")
        return msisdn_from_e164(phone)
    if not body.phoneNumber:
        raise errors.missing_identifier("The phone number cannot be identified.")
    supi = await request.app.state.resolver.supi_for_phone(body.phoneNumber)
    if supi is None:
        raise errors.identifier_not_found("The phone number is not known to the network.")
    return msisdn_from_e164(body.phoneNumber)


async def _require_device_known(request: Request, msisdn: str) -> None:
    """Device swap needs a device: a SIM that has never registered (no IMEISV) -> 422."""
    from ..swap_history import imei_of
    store = request.app.state.store
    doc = await asyncio.to_thread(store.subscribers.find_one, {"msisdn": msisdn}, {"imeisv": 1})
    if not doc or imei_of(doc.get("imeisv")) is None:
        raise errors.service_not_applicable("The phone number has never connected a device to the network.")


def _monitored_days(since: datetime | None) -> int:
    if since is None:
        return 1
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    days = (datetime.now(timezone.utc) - since).total_seconds() / 86400
    return max(1, min(999, math.ceil(days)))


def make_router(api: str, version: str, etype: str, field: str, scope_base: str) -> APIRouter:
    router = APIRouter(prefix=f"/{api}/{version}", tags=[api])

    @router.post("/retrieve-date", name=f"{api}-retrieve-date")
    async def retrieve_date(body: DateRequest, request: Request,
                            p: Principal = Depends(require_any_scope(f"{scope_base}:retrieve-date", scope_base))):
        msisdn = await _phone(request, body, p)
        if etype == "DEVICE_SWAP":
            await _require_device_known(request, msisdn)
        hist = request.app.state.swaps
        ts = await asyncio.to_thread(hist.latest, etype, msisdn)
        if ts is not None:
            return {field: _iso(ts)}
        since = await asyncio.to_thread(hist.monitoring_since)
        return {field: None, "monitoredPeriod": _monitored_days(since)}

    @router.post("/check", name=f"{api}-check")
    async def check(body: CheckRequest, request: Request,
                    p: Principal = Depends(require_any_scope(f"{scope_base}:check", scope_base))):
        limit = min(2400, request.app.state.config.swap.max_monitored_hours)
        if body.maxAge > limit:
            raise errors.CamaraError(400, "OUT_OF_RANGE", f"maxAge exceeds the supported monitored period ({limit} h).")
        msisdn = await _phone(request, body, p)
        if etype == "DEVICE_SWAP":
            await _require_device_known(request, msisdn)
        ts = await asyncio.to_thread(request.app.state.swaps.latest, etype, msisdn)
        if ts is not None and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        swapped = ts is not None and ts >= datetime.now(timezone.utc) - timedelta(hours=body.maxAge)
        return {"swapped": swapped}

    return router


sim_swap_router = make_router("sim-swap", "v2", "SIM_SWAP", "latestSimChange", "sim-swap")
device_swap_router = make_router("device-swap", "v1", "DEVICE_SWAP", "latestDeviceChange", "device-swap")
