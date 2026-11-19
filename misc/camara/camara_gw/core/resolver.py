"""Device resolution: CAMARA Device object <-> SUPI <-> MSISDN <-> UE IP.

Sources:
- phoneNumber  -> SUPI : MongoDB subscribers.msisdn
- ipv4/ipv6    -> SUPI : SMF /pdu-info (live PDU sessions)
- SUPI -> MSISDN       : MongoDB
- SUPI -> UE IPs       : SMF /pdu-info

The lab has no NAT between UE and core, so ipv4Address.privateAddress and
publicAddress are both treated as the UE (PDU session) address.
"""

from __future__ import annotations

import asyncio
import ipaddress
from dataclasses import dataclass, field

from .. import errors
from ..models import Device
from .info import InfoClient, InfoUnavailable
from .mongo import Store, e164_from_msisdn, msisdn_from_e164


@dataclass
class PduSession:
    psi: int | None
    dnn: str | None
    ipv4: str | None
    ipv6: str | None
    snssai: dict | None
    qos_flows: list[dict] = field(default_factory=list)
    state: str | None = None


@dataclass
class ResolvedDevice:
    supi: str                                   # 'imsi-<digits>'
    phone_number: str | None                    # E.164 with '+', first MSISDN if any
    sessions: list[PduSession] = field(default_factory=list)
    matched_by: list[str] = field(default_factory=list)

    @property
    def imsi(self) -> str:
        return self.supi.removeprefix("imsi-")

    @property
    def ipv4_addresses(self) -> list[str]:
        return [s.ipv4 for s in self.sessions if s.ipv4]


def _sessions_of(item: dict) -> list[PduSession]:
    out = []
    for p in item.get("pdu", []) or []:
        out.append(PduSession(psi=p.get("psi"), dnn=p.get("dnn"), ipv4=p.get("ipv4"), ipv6=p.get("ipv6"),
                              snssai=p.get("snssai"), qos_flows=p.get("qos_flows") or [],
                              state=p.get("pdu_state")))
    return out


def _ipv6_match(device_ip: str, pdu_ipv6: str) -> bool:
    try:
        dev = ipaddress.IPv6Address(device_ip)
        net = ipaddress.IPv6Network(pdu_ipv6 if "/" in pdu_ipv6 else f"{pdu_ipv6}/64", strict=False)
        return dev in net
    except ValueError:
        return False


class Resolver:
    def __init__(self, store: Store, info: InfoClient):
        self.store = store
        self.info = info

    async def _pdu_by_supi(self) -> dict[str, list[PduSession]]:
        try:
            items = await self.info.pdu_info()
        except InfoUnavailable as exc:
            raise errors.unavailable(f"SMF session info unavailable: {exc}")
        return {it["supi"]: _sessions_of(it) for it in items if it.get("supi")}

    async def supi_for_phone(self, phone: str) -> str | None:
        msisdn = msisdn_from_e164(phone)
        n = await asyncio.to_thread(self.store.count_by_msisdn, msisdn)
        if n > 1:
            raise errors.service_not_applicable(f"Phone number maps to {n} subscribers in the lab DB.")
        doc = await asyncio.to_thread(self.store.subscriber_by_msisdn, msisdn)
        return f"imsi-{doc['imsi']}" if doc else None

    async def phone_for_supi(self, supi: str) -> str | None:
        doc = await asyncio.to_thread(self.store.subscriber_by_imsi, supi.removeprefix("imsi-"))
        msisdns = (doc or {}).get("msisdn") or []
        return e164_from_msisdn(msisdns[0]) if msisdns else None

    async def resolve(self, device: Device | None, *, require_session: bool = False) -> ResolvedDevice:
        """Resolve a CAMARA Device to a single subscriber, enforcing identifier consistency."""
        if device is None or device.is_empty():
            raise errors.missing_identifier()
        if device.networkAccessIdentifier and not (device.phoneNumber or device.ipv4Address or device.ipv6Address):
            raise errors.unsupported_identifier("networkAccessIdentifier is not supported by this lab.")

        candidates: dict[str, str] = {}          # identifier kind -> SUPI
        pdu = None

        if device.phoneNumber:
            supi = await self.supi_for_phone(device.phoneNumber)
            if supi is None:
                raise errors.identifier_not_found()
            candidates["phoneNumber"] = supi

        if device.ipv4Address or device.ipv6Address:
            pdu = await self._pdu_by_supi()
            if device.ipv4Address:
                addrs = {a for a in (device.ipv4Address.privateAddress, device.ipv4Address.publicAddress) if a}
                hit = next((s for s, ss in pdu.items() if any(x.ipv4 in addrs for x in ss)), None)
                if hit is None:
                    raise errors.identifier_not_found()
                candidates["ipv4Address"] = hit
            if device.ipv6Address:
                hit = next((s for s, ss in pdu.items()
                            if any(x.ipv6 and _ipv6_match(device.ipv6Address, x.ipv6) for x in ss)), None)
                if hit is None:
                    raise errors.identifier_not_found()
                candidates["ipv6Address"] = hit

        if len(set(candidates.values())) > 1:
            raise errors.identifier_mismatch()
        supi = next(iter(candidates.values()))

        if pdu is None:
            pdu = await self._pdu_by_supi()
        sessions = pdu.get(supi, [])
        if require_session and not sessions:
            raise errors.service_not_applicable("Device has no active PDU session.")

        phone = device.phoneNumber or await self.phone_for_supi(supi)
        return ResolvedDevice(supi=supi, phone_number=phone, sessions=sessions, matched_by=sorted(candidates))
