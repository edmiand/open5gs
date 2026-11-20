"""CAMARA Number Verification (NumberVerification r4.2, number-verification v2.1.1).

Needs a 3-legged access token from the network-based authorization-code flow
(auth/network_auth.py): sub = SUPI, amr contains "network". Tokens are single-use.

  POST /verify               {phoneNumber} | {hashedPhoneNumber}  -> {devicePhoneNumberVerified}
  GET  /device-phone-number                                       -> {devicePhoneNumber}
"""

from __future__ import annotations

import asyncio
import hashlib
import re

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .. import errors
from ..auth.network_auth import AMR_NETWORK, consume_token
from ..auth.tokens import Principal, require_scopes
from ..core.mongo import e164_from_msisdn
from ..models import PHONE_PATTERN

router = APIRouter(prefix="/number-verification/v2", tags=["number-verification"])
HASH_RE = re.compile(r"^[a-fA-F0-9]{64}$")


class VerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phoneNumber: str | None = Field(None, pattern=PHONE_PATTERN)
    hashedPhoneNumber: str | None = Field(None, pattern=r"^[a-fA-F0-9]{64}$")

    @model_validator(mode="after")
    def _exactly_one(self):
        if (self.phoneNumber is None) == (self.hashedPhoneNumber is None):
            raise ValueError("exactly one of phoneNumber or hashedPhoneNumber is required")
        return self


async def _device_numbers(request: Request, p: Principal) -> list[str]:
    """E.164 numbers currently allocated to the token's device; enforces NV token rules."""
    if not p.sub:
        raise errors.CamaraError(403, "NUMBER_VERIFICATION.USER_NOT_AUTHENTICATED_BY_MOBILE_NETWORK",
                                 "Client must authenticate via the mobile network to use this service.")
    if AMR_NETWORK not in ((p.claims or {}).get("amr") or []):
        raise errors.CamaraError(403, "NUMBER_VERIFICATION.USER_NOT_AUTHENTICATED_BY_MOBILE_NETWORK",
                                 "Client must authenticate via the mobile network to use this service.")
    await consume_token(request, p)
    store = request.app.state.store
    doc = await asyncio.to_thread(store.subscriber_by_imsi, p.sub.removeprefix("imsi-"))
    return [e164_from_msisdn(m) for m in ((doc or {}).get("msisdn") or [])]


@router.post("/verify")
async def verify(body: VerifyRequest, request: Request,
                 p: Principal = Depends(require_scopes("number-verification:verify"))):
    numbers = await _device_numbers(request, p)
    if body.phoneNumber is not None:
        ok = body.phoneNumber in numbers
    else:
        want = body.hashedPhoneNumber.lower()
        ok = any(hashlib.sha256(n.encode()).hexdigest() == want for n in numbers)
    return {"devicePhoneNumberVerified": ok}


@router.get("/device-phone-number")
async def device_phone_number(request: Request,
                              p: Principal = Depends(require_scopes("number-verification:device-phone-number:read"))):
    numbers = await _device_numbers(request, p)
    if not numbers:
        raise errors.CamaraError(403, "PERMISSION_DENIED", "No phone number is allocated to this device.")
    return {"devicePhoneNumber": numbers[0]}
