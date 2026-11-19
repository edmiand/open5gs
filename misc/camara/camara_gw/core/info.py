"""Clients for Open5GS metrics-port JSON endpoints (HTTP/1.1):

- SMF  /pdu-info : per-UE PDU sessions {supi, pdu:[{psi,dnn,ipv4,ipv6,snssai,qos_flows,pdu_state}], ue_activity}
- AMF  /ue-info  : per-UE registration {supi, pei, msisdn, cm_state, location:{nr_tai:{plmn,tac}}, ...}

These are Open5GS-specific (not 3GPP) — keep all knowledge of their shape in this module.

Paging logic adapted from ~/open5gs-mcp src/tools/list_ue_sessions.py (_fetch_all_pages),
same author; made async and error-raising for the gateway.
"""

from __future__ import annotations

import httpx

from ..config import Info


class InfoUnavailable(RuntimeError):
    pass


class InfoClient:
    def __init__(self, cfg: Info, transport: httpx.AsyncBaseTransport | None = None):
        self.cfg = cfg
        self._client = httpx.AsyncClient(timeout=cfg.timeout, transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _all_pages(self, base: str, path: str, page_size: int = 100) -> list[dict]:
        items: list[dict] = []
        page = 0
        while True:
            try:
                r = await self._client.get(f"{base}{path}", params={"page": page, "page_size": page_size})
                r.raise_for_status()
                data = r.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise InfoUnavailable(f"{base}{path}: {exc}") from exc
            batch = data.get("items", [])
            items.extend(batch)
            if len(batch) < page_size:
                return items
            page += 1

    async def pdu_info(self) -> list[dict]:
        return await self._all_pages(self.cfg.smf, "/pdu-info")

    async def ue_info(self) -> list[dict]:
        return await self._all_pages(self.cfg.amf, "/ue-info")

    async def upf_metrics(self) -> str:
        try:
            r = await self._client.get(f"{self.cfg.upf}/metrics")
            r.raise_for_status()
            return r.text
        except httpx.HTTPError as exc:
            raise InfoUnavailable(f"{self.cfg.upf}/metrics: {exc}") from exc
