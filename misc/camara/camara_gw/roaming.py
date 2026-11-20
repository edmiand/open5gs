"""Device roaming status from the AMF, with change detection and a lab override.

roaming = serving PLMN (AMF /ue-info location.nr_tai.plmn) is not one of the home PLMNs
(config home_plmn, e.g. "99970"). countryCode = serving MCC; countryName = MCC -> ISO list.

Upstream Open5GS roaming can't be exercised with UERANSIM (docs/_docs/tutorial/05-roaming.md),
so the lab is always "home". `camara_overrides` (lab endpoint) can pin a SUPI to a fake
serving PLMN to demo roaming-on / change-country / roaming-off events. Overridden results are
logged as such.

State per SUPI is kept in `camara_roaming_state` so retrieve can answer with the last known
status (and its lastStatusTime) when the UE is not currently known to the AMF.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable

from .core.info import InfoClient, InfoUnavailable
from .mcc import country_of

log = logging.getLogger("camara.roaming")

STATE = "camara_roaming_state"
OVERRIDES = "camara_overrides"

Listener = Callable[[str, dict | None, dict], Awaitable[None]]   # (supi, previous, current)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def status_for(serving_plmn: str, home_plmns: list[str]) -> dict:
    mcc = int(serving_plmn[:3])
    roaming = serving_plmn not in home_plmns
    st = {"roaming": roaming, "servingPlmn": serving_plmn}
    if roaming:
        st["countryCode"] = mcc
        st["countryName"] = country_of(mcc)
    return st


class RoamingMonitor:
    def __init__(self, store, info: InfoClient, home_plmns: list[str], interval: float = 2.0):
        self.store = store
        self.info = info
        self.home = home_plmns
        self.interval = interval
        self.state = store.db[STATE]
        self.overrides = store.db[OVERRIDES]
        self.listeners: list[Listener] = []
        self._task: asyncio.Task | None = None

    async def current(self) -> dict[str, dict]:
        """SUPI -> {roaming, servingPlmn, countryCode?, countryName?, lastStatusTime, overridden}."""
        items = await self.info.ue_info()
        ovr = await asyncio.to_thread(lambda: {d["_id"]: d for d in self.overrides.find({})})
        out = {}
        for it in items:
            supi = it.get("supi")
            plmn = ((it.get("location") or {}).get("nr_tai") or {}).get("plmn")
            if not supi or not plmn:
                continue
            ts_us = (it.get("location") or {}).get("timestamp")
            ts = datetime.fromtimestamp(ts_us / 1e6, timezone.utc) if ts_us else _now()
            overridden = supi in ovr
            if overridden:
                plmn = ovr[supi]["plmn"]
                ts = _now()                     # the override is "confirmed" continuously
            st = status_for(plmn, self.home)
            st.update({"lastStatusTime": ts, "overridden": overridden})
            out[supi] = st
        return out

    async def lookup(self, supi: str) -> dict | None:
        """Live status if the AMF knows the UE; else the last recorded one; else None."""
        try:
            live = await self.current()
        except InfoUnavailable:
            live = {}
        if supi in live:
            return live[supi]
        return await asyncio.to_thread(self.state.find_one, {"_id": supi}, {"_id": 0})

    async def poll_once(self) -> list[tuple[str, dict | None, dict]]:
        changes = []
        cur = await self.current()
        for supi, st in cur.items():
            prev = await asyncio.to_thread(self.state.find_one, {"_id": supi}, {"_id": 0})
            key = ("roaming", "servingPlmn")
            if prev is None or any(prev.get(k) != st.get(k) for k in key):
                changes.append((supi, prev, st))
            await asyncio.to_thread(self.state.replace_one, {"_id": supi}, {"_id": supi, **st}, upsert=True)
        for supi, prev, st in changes:
            if prev is not None:
                log.info("roaming change %s: %s -> %s%s", supi, prev.get("servingPlmn"), st["servingPlmn"],
                         " (lab override)" if st.get("overridden") else "")
        return changes

    async def run(self) -> None:
        while True:
            try:
                for supi, prev, st in await self.poll_once():
                    if prev is None:          # first sighting establishes the baseline, no events
                        continue
                    for fn in self.listeners:
                        try:
                            await fn(supi, prev, st)
                        except Exception:
                            log.exception("roaming listener failed")
            except asyncio.CancelledError:
                raise
            except InfoUnavailable as exc:
                log.warning("roaming poll: %s", exc)
            except Exception:
                log.exception("roaming poll failed")
            await asyncio.sleep(self.interval)

    def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    # ── lab override ─────────────────────────────────────────────────────────
    async def set_override(self, supi: str, plmn: str | None) -> None:
        if plmn is None:
            await asyncio.to_thread(self.overrides.delete_one, {"_id": supi})
        else:
            await asyncio.to_thread(self.overrides.replace_one, {"_id": supi},
                                    {"_id": supi, "plmn": plmn, "set": _now()}, upsert=True)
