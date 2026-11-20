"""SIM / device swap history derived from Open5GS `subscribers` (read-only).

Open5GS keeps only current state: `msisdn[]` per IMSI and the last `imeisv` (written by the
UDR on each registration, src/udr/nudr-handler.c). A poller snapshots both and records changes
in `camara_events`:

- SIM_SWAP    : an MSISDN is now served by a different IMSI than last seen
                (WebUI "move number to another SIM"; a disappearing MSISDN keeps its last IMSI,
                so remove-then-add is detected as one swap).
- DEVICE_SWAP : the IMEI (first 14 digits of IMEISV = TAC+SNR) seen for an MSISDN changed.
                A software-version-only change (last 2 digits) is not a device swap.

Timestamps are detection times (poll interval granularity). History starts when monitoring
starts (`camara_snapshots.monitoring_since`); before that nothing is known.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable

from .core.mongo import Store

log = logging.getLogger("camara.swap")

EVENTS = "camara_events"
SNAPSHOTS = "camara_snapshots"
SNAP_ID = "subscribers-swap"

Listener = Callable[[dict], Awaitable[None]]


def imei_of(imeisv) -> str | None:
    """IMEISV (16 digits) or IMEI (14/15) -> 14-digit IMEI (TAC+SNR); None if unknown."""
    if isinstance(imeisv, list):
        imeisv = imeisv[0] if imeisv else None
    if not imeisv or not isinstance(imeisv, str):
        return None
    digits = imeisv.removeprefix("imeisv-").removeprefix("imei-")
    return digits[:14] if len(digits) >= 14 and digits.isdigit() else None


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SwapHistory:
    def __init__(self, store: Store, interval: float = 2.0):
        self.store = store
        self.interval = interval
        self.events = store.db[EVENTS]
        self.snaps = store.db[SNAPSHOTS]
        self.listeners: list[Listener] = []
        self._task: asyncio.Task | None = None

    # ── polling ──────────────────────────────────────────────────────────────
    def _current(self) -> tuple[dict[str, str], dict[str, str]]:
        msisdn_imsi: dict[str, str] = {}
        imsi_imei: dict[str, str] = {}
        for d in self.store.subscribers.find({}, {"_id": 0, "imsi": 1, "msisdn": 1, "imeisv": 1}):
            imsi = d.get("imsi")
            if not imsi:
                continue
            imei = imei_of(d.get("imeisv"))
            if imei:
                imsi_imei[imsi] = imei
            for m in d.get("msisdn") or []:
                msisdn_imsi[m] = imsi
        return msisdn_imsi, imsi_imei

    def poll_once(self) -> list[dict]:
        """One diff pass (sync). Returns newly recorded events."""
        now = _now()
        msisdn_imsi, imsi_imei = self._current()
        snap = self.snaps.find_one({"_id": SNAP_ID})
        if snap is None:
            msisdn_imei = {m: imsi_imei[i] for m, i in msisdn_imsi.items() if i in imsi_imei}
            self.snaps.insert_one({"_id": SNAP_ID, "monitoring_since": now, "msisdn_imsi": msisdn_imsi,
                                   "msisdn_imei": msisdn_imei, "updated": now})
            log.info("swap monitoring baseline: %d MSISDNs", len(msisdn_imsi))
            return []

        last_imsi: dict = dict(snap.get("msisdn_imsi", {}))
        last_imei: dict = dict(snap.get("msisdn_imei", {}))
        new_events: list[dict] = []
        for m, imsi in msisdn_imsi.items():
            prev = last_imsi.get(m)
            if prev is not None and prev != imsi:
                new_events.append({"type": "SIM_SWAP", "msisdn": m, "old": prev, "new": imsi})
            last_imsi[m] = imsi                     # keep disappeared MSISDNs (no delete)
            imei = imsi_imei.get(imsi)
            if imei is None:
                continue
            prev_imei = last_imei.get(m)
            if prev_imei is not None and prev_imei != imei:
                new_events.append({"type": "DEVICE_SWAP", "msisdn": m, "old": prev_imei, "new": imei})
            last_imei[m] = imei

        for ev in new_events:
            ev.update({"_id": str(uuid.uuid4()), "ts": now})
            self.events.insert_one(ev)
            log.info("%s msisdn=%s %s -> %s", ev["type"], ev["msisdn"], ev["old"], ev["new"])
        self.snaps.update_one({"_id": SNAP_ID}, {"$set": {"msisdn_imsi": last_imsi, "msisdn_imei": last_imei,
                                                          "updated": now}})
        return new_events

    async def run(self) -> None:
        while True:
            try:
                for ev in await asyncio.to_thread(self.poll_once):
                    for fn in self.listeners:
                        try:
                            await fn(ev)
                        except Exception:
                            log.exception("swap listener failed")
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("swap poll failed")
            await asyncio.sleep(self.interval)

    def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    # ── queries ──────────────────────────────────────────────────────────────
    def latest(self, etype: str, msisdn: str) -> datetime | None:
        d = self.events.find_one({"type": etype, "msisdn": msisdn}, sort=[("ts", -1)])
        return d["ts"] if d else None

    def monitoring_since(self) -> datetime | None:
        s = self.snaps.find_one({"_id": SNAP_ID}, {"monitoring_since": 1})
        return s["monitoring_since"] if s else None
