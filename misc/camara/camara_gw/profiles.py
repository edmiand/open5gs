"""QoS profile catalog (profiles.yaml) and its CAMARA QosProfile rendering."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

PROFILES_PATH = Path(__file__).resolve().parent / "profiles.yaml"
_RATE_UNITS = [("Tbps", 10**12), ("Gbps", 10**9), ("Mbps", 10**6), ("kbps", 10**3), ("bps", 1)]


def rate(bps: int | None) -> dict | None:
    """bps -> CAMARA Rate {value<=1024, unit} using the largest exact unit."""
    if bps is None:
        return None
    for unit, mult in _RATE_UNITS:
        if bps % mult == 0 and bps // mult <= 1024:
            return {"value": bps // mult, "unit": unit}
    for unit, mult in reversed(_RATE_UNITS):     # fallback: smallest unit that fits
        if bps // mult <= 1024:
            return {"value": bps // mult, "unit": unit}
    raise ValueError(f"rate {bps} bps not representable")


def bitrate_str(bps: int) -> str:
    """bps -> 3GPP BitRate string (TS 29.571), e.g. '2 Mbps'."""
    for unit, mult in _RATE_UNITS[1:]:          # Gbps..bps (no Tbps in 29.571 pattern)
        if bps % mult == 0:
            return f"{bps // mult} {unit}"
    return f"{bps} bps"


@dataclass(frozen=True)
class Profile:
    name: str
    description: str
    fiveqi: int
    arp_priority: int
    targetMinDownstreamRate: int
    maxDownstreamRate: int
    targetMinUpstreamRate: int
    maxUpstreamRate: int
    packetDelayBudget: int | None = None
    jitter: int | None = None
    packetErrorLossRate: int | None = None
    priority: int | None = None
    minDuration: int = 1
    maxDuration: int = 86400
    status: str = "ACTIVE"

    def to_camara(self) -> dict:
        out = {
            "name": self.name, "description": self.description, "status": self.status,
            "targetMinUpstreamRate": rate(self.targetMinUpstreamRate),
            "maxUpstreamRate": rate(self.maxUpstreamRate),
            "targetMinDownstreamRate": rate(self.targetMinDownstreamRate),
            "maxDownstreamRate": rate(self.maxDownstreamRate),
            "minDuration": {"value": self.minDuration, "unit": "Seconds"},
            "maxDuration": {"value": self.maxDuration, "unit": "Seconds"},
            "priority": self.priority,
            "packetDelayBudget": {"value": self.packetDelayBudget, "unit": "Milliseconds"}
            if self.packetDelayBudget else None,
            "jitter": {"value": self.jitter, "unit": "Milliseconds"} if self.jitter else None,
            "packetErrorLossRate": self.packetErrorLossRate,
        }
        return {k: v for k, v in out.items() if v is not None}


def load_profiles(path: Path = PROFILES_PATH) -> dict[str, Profile]:
    raw = yaml.safe_load(path.read_text())["profiles"]
    return {p["name"]: Profile(**p) for p in raw}
