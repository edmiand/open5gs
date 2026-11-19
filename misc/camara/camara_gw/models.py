"""Shared CAMARA Commonalities schemas (Device, DeviceIpv4Addr)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

PHONE_PATTERN = r"^\+[1-9][0-9]{4,14}$"


class DeviceIpv4Addr(BaseModel):
    model_config = ConfigDict(extra="forbid")
    publicAddress: str | None = None
    privateAddress: str | None = None
    publicPort: int | None = Field(None, ge=0, le=65535)

    @model_validator(mode="after")
    def _need_one(self):
        if not (self.publicAddress or self.privateAddress):
            raise ValueError("ipv4Address requires publicAddress and/or privateAddress")
        return self


class Device(BaseModel):
    """CAMARA Device object. At least one identifier must be present."""

    model_config = ConfigDict(extra="forbid")
    phoneNumber: str | None = Field(None, pattern=PHONE_PATTERN)
    networkAccessIdentifier: str | None = None
    ipv4Address: DeviceIpv4Addr | None = None
    ipv6Address: str | None = None

    def is_empty(self) -> bool:
        return not (self.phoneNumber or self.networkAccessIdentifier or self.ipv4Address or self.ipv6Address)
