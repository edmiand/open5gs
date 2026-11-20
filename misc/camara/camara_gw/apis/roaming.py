"""CAMARA Device Roaming Status (DeviceRoamingStatus r2.3): device-roaming-status v1.2.0 and
device-roaming-status-subscriptions v0.9.0, plus a lab override endpoint.

Events (subscriptions):
  roaming-status         {roaming, countryCode?, countryName?}   on roaming on<->off
  roaming-on / -off      {device?}                              on the corresponding transition
  roaming-change-country {countryCode, countryName}             while roaming, serving MCC changed
"""

from __future__ import annotations

import logging
import re
from datetime import timezone

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from .. import errors
from ..auth.tokens import Principal, require_scopes
from ..models import Device
from ..subscriptions import SubscriptionManager, make_subscription_router

log = logging.getLogger("camara.roaming")

router = APIRouter(prefix="/device-roaming-status/v1", tags=["device-roaming-status"])
lab_router = APIRouter(prefix="/_lab", tags=["lab"])

SUB_API = "device-roaming-status-subscriptions"
EV = "v0"
T = lambda n: f"org.camaraproject.{SUB_API}.{EV}.{n}"  # noqa: E731
ROAMING_STATUS, ROAMING_ON, ROAMING_OFF, CHANGE_COUNTRY = (T("roaming-status"), T("roaming-on"), T("roaming-off"),
                                                           T("roaming-change-country"))


def _iso(dt) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _country(st: dict) -> dict:
    return {k: st[k] for k in ("countryCode", "countryName") if k in st}


class RoamingStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device: Device | None = None


@router.post("/retrieve")
async def retrieve(body: RoamingStatusRequest, request: Request,
                   p: Principal = Depends(require_scopes("device-roaming-status:read"))):
    st_ = request.app.state
    if body.device is not None and p.sub:
        raise errors.CamaraError(422, "UNNECESSARY_IDENTIFIER", "The device is already identified by the access token.")
    rd = await st_.resolver.resolve(body.device)
    status = await st_.roaming.lookup(rd.supi)
    if status is None:
        raise errors.service_not_applicable("No roaming status is known for this device (never seen by the AMF).")
    out = {"lastStatusTime": _iso(status["lastStatusTime"]), "roaming": status["roaming"], **_country(status)}
    if body.device is not None:
        out = {"device": rd.device_response, **out}
    return out


# ── subscriptions ────────────────────────────────────────────────────────────
async def _resolve_subject(request: Request, detail: dict, p: Principal) -> tuple[dict, dict]:
    extra = set(detail or {}) - {"device"}
    if extra:
        raise errors.invalid_argument(f"Unknown subscriptionDetail field(s): {', '.join(sorted(extra))}")
    raw = (detail or {}).get("device")
    if raw is not None and p.sub:
        raise errors.CamaraError(422, "UNNECESSARY_IDENTIFIER", "The device is already identified by the access token.")
    try:
        device = Device.model_validate(raw) if raw is not None else None
    except ValueError as exc:
        raise errors.invalid_argument(f"subscriptionDetail.device: {exc}")
    rd = await request.app.state.resolver.resolve(device)
    return {"supi": rd.supi}, {"device": rd.device_response}


async def _initial_event(request: Request, doc: dict) -> None:
    """initialEvent=true: emit now if the current situation matches the subscribed event."""
    st = await request.app.state.roaming.lookup(doc["subject"]["supi"])
    if st is None:
        return
    etype = doc["types"][0]
    data = None
    if etype == ROAMING_STATUS:
        data = {"roaming": st["roaming"], **_country(st)}
    elif etype == ROAMING_ON and st["roaming"]:
        data = {}
    elif etype == ROAMING_OFF and not st["roaming"]:
        data = {}
    if data is not None:
        payload = {"device": doc["detail"]["device"], **data, "subscriptionId": doc["_id"]}
        request.app.state.notifier.send(doc["sink"], doc.get("sinkCredential"), etype, payload)


subs_router = make_subscription_router(api=SUB_API, version="v0.9", event_version=EV,
                                       event_names=["roaming-status", "roaming-on", "roaming-off",
                                                    "roaming-change-country"],
                                       resolve_subject=_resolve_subject, on_created=_initial_event)


def roaming_listener(subs: SubscriptionManager):
    async def on_change(supi: str, prev: dict, cur: dict) -> None:
        subject = {"supi": supi}
        if prev["roaming"] != cur["roaming"]:
            await subs.emit(SUB_API, ROAMING_STATUS, subject, {"roaming": cur["roaming"], **_country(cur)})
            await subs.emit(SUB_API, ROAMING_ON if cur["roaming"] else ROAMING_OFF, subject, {})
        elif cur["roaming"] and prev.get("countryCode") != cur.get("countryCode"):
            await subs.emit(SUB_API, CHANGE_COUNTRY, subject, _country(cur))
    return on_change


# ── lab override ─────────────────────────────────────────────────────────────
class RoamingOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device: Device
    servingPlmn: str | None = Field(None, pattern=r"^\d{5,6}$")   # null clears the override


@lab_router.post("/roaming-override")
async def roaming_override(body: RoamingOverride, request: Request,
                           _: Principal = Depends(require_scopes("lab:write"))):
    """Pretend the device is served by `servingPlmn` (MCC+MNC). Lab demo only."""
    rd = await request.app.state.resolver.resolve(body.device)
    await request.app.state.roaming.set_override(rd.supi, body.servingPlmn)
    log.warning("LAB OVERRIDE %s serving PLMN -> %s", rd.supi, body.servingPlmn or "(cleared)")
    return {"supi": rd.supi, "servingPlmn": body.servingPlmn, "overridden": body.servingPlmn is not None}
