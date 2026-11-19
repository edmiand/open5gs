"""CAMARA QoS Profiles API (QoSProfiles r1.2, qos-profiles v1.2.0)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from .. import errors
from ..auth.tokens import Principal, require_scopes
from ..models import Device

router = APIRouter(prefix="/qos-profiles/v1", tags=["qos-profiles"])
SCOPE = "qos-profiles:read"
NAME_PATTERN = r"^[a-zA-Z0-9_.-]+$"


class QosProfileDeviceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device: Device | None = None
    name: str | None = Field(None, min_length=3, max_length=256, pattern=NAME_PATTERN)
    status: str | None = Field(None, pattern="^(ACTIVE|INACTIVE|DEPRECATED)$")


@router.post("/retrieve-qos-profiles")
async def retrieve_qos_profiles(body: QosProfileDeviceRequest, request: Request,
                                p: Principal = Depends(require_scopes(SCOPE))):
    st = request.app.state
    if body.device is not None:
        if p.sub:
            raise errors.CamaraError(422, "UNNECESSARY_IDENTIFIER",
                                     "The device is already identified by the access token.")
        # All lab profiles are available to any subscriber that exists; resolution validates the device.
        await st.resolver.resolve(body.device)
    out = [pr.to_camara() for pr in st.profiles.values()
           if (body.name is None or pr.name == body.name) and (body.status is None or pr.status == body.status)]
    if not out:
        raise errors.CamaraError(404, "NOT_FOUND", "No QoS profiles match the given criteria.")
    return out


@router.get("/qos-profiles/{name}")
async def get_qos_profile(name: str, request: Request, _: Principal = Depends(require_scopes(SCOPE))):
    pr = request.app.state.profiles.get(name)
    if pr is None:
        raise errors.CamaraError(404, "NOT_FOUND", f"QoS profile {name} not found.")
    return pr.to_camara()
