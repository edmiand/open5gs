"""5GC SBI client (HTTP/2 cleartext, prior knowledge).

Open5GS SBI servers speak h2c only. httpx needs `http1=False, http2=True` to send the
HTTP/2 preface directly; plain `http2=True` falls back to HTTP/1.1 over cleartext and
the NFs reject it ("illegal request line" / disconnect). Verified in Phase 0 spike.
"""

from __future__ import annotations

import httpx

from ..config import Sbi


class SbiClient:
    def __init__(self, cfg: Sbi, transport: httpx.AsyncBaseTransport | None = None):
        self.cfg = cfg
        self._client = httpx.AsyncClient(http1=False, http2=True, timeout=cfg.timeout,
                                         transport=transport,
                                         headers={"user-agent": "open5gs-camara",
                                                  "accept": "application/json"})

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(self, method: str, url: str, **kw) -> httpx.Response:
        return await self._client.request(method, url, **kw)

    # ── NRF ──────────────────────────────────────────────────────────────────
    async def nrf_profiles(self, nf_type: str) -> list[dict]:
        """NF profiles of `nf_type` via Nnrf_NFManagement (not discovery: Open5GS filters
        discovery by the target's allowedNfTypes, which hides e.g. UDM from an AF)."""
        r = await self.request("GET", f"{self.cfg.nrf}/nnrf-nfm/v1/nf-instances", params={"nf-type": nf_type})
        r.raise_for_status()
        hrefs = [i["href"] for i in r.json().get("_links", {}).get("item", []) if i.get("href")]
        profiles = []
        for h in hrefs:
            p = await self.request("GET", h)
            p.raise_for_status()
            profiles.append(p.json())
        return profiles

    # ── BSF ──────────────────────────────────────────────────────────────────
    async def bsf_binding_by_ipv4(self, ipv4: str) -> dict | None:
        """Nbsf_Management discovery. Returns PcfBinding or None (404/204 = no binding)."""
        r = await self.request("GET", f"{self.cfg.bsf}/nbsf-management/v1/pcfBindings",
                               params={"ipv4Addr": ipv4})
        if r.status_code in (204, 404):
            return None
        r.raise_for_status()
        return r.json()

    # ── UDM ──────────────────────────────────────────────────────────────────
    async def udm_amf_registration(self, supi: str) -> dict | None:
        r = await self.request("GET", f"{self.cfg.udm}/nudm-uecm/v1/{supi}/registrations/amf-3gpp-access")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
