"""CAMARA SIM Swap Subscriptions (SimSwap r4.3, sim-swap-subscriptions v0.4.0).

Event: org.camaraproject.sim-swap-subscriptions.v0.swapped, data {phoneNumber, subscriptionId}.
Fed by the swap-history poller (SIM_SWAP events).
"""

from __future__ import annotations

from fastapi import Request

from .. import errors
from ..auth.tokens import Principal
from ..core.mongo import E164_RE, e164_from_msisdn, msisdn_from_e164
from ..subscriptions import SubscriptionManager, make_subscription_router

API = "sim-swap-subscriptions"
EVENT_VERSION = "v0"
SWAPPED = f"org.camaraproject.{API}.{EVENT_VERSION}.swapped"


async def _resolve(request: Request, detail: dict, p: Principal) -> tuple[dict, dict]:
    phone = (detail or {}).get("phoneNumber")
    extra = set(detail or {}) - {"phoneNumber"}
    if extra:
        raise errors.invalid_argument(f"Unknown subscriptionDetail field(s): {', '.join(sorted(extra))}")
    if phone and p.sub:
        raise errors.CamaraError(422, "UNNECESSARY_IDENTIFIER", "The phone number is already identified by the access token.")
    if not phone:
        raise errors.missing_identifier("The phone number cannot be identified.")
    if not E164_RE.match(phone):
        raise errors.invalid_argument("subscriptionDetail.phoneNumber must be E.164 (+digits).")
    if await request.app.state.resolver.supi_for_phone(phone) is None:
        raise errors.identifier_not_found("The phone number is not known to the network.")
    return {"msisdn": msisdn_from_e164(phone)}, {"phoneNumber": phone}


router = make_subscription_router(api=API, version="v0.4", event_version=EVENT_VERSION,
                                  event_names=["swapped"], resolve_subject=_resolve)


def swap_listener(subs: SubscriptionManager):
    async def on_event(ev: dict) -> None:
        if ev["type"] == "SIM_SWAP":
            await subs.emit(API, SWAPPED, {"msisdn": ev["msisdn"]}, {"phoneNumber": e164_from_msisdn(ev["msisdn"])})
    return on_event
