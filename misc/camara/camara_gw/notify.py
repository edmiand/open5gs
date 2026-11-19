"""CloudEvents delivery to API-consumer sinks (fire-and-forget with retries)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from . import errors
from .config import Notify

log = logging.getLogger("camara.notify")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def validate_sink(cfg: Notify, sink: str | None, sink_credential: dict | None) -> None:
    """Raise CAMARA errors for unusable sink / credential (QoD: 400 INVALID_SINK / INVALID_CREDENTIAL)."""
    if sink is None:
        if sink_credential is not None:
            raise errors.CamaraError(400, "INVALID_ARGUMENT", "sinkCredential provided without sink.")
        return
    u = urlparse(sink)
    allowed = {"https", "http"} if cfg.allow_http_sink else {"https"}
    if u.scheme not in allowed or not u.netloc:
        raise errors.CamaraError(400, "INVALID_SINK", f"sink must be an {' or '.join(sorted(allowed))} URL.")
    if sink_credential is not None:
        ctype = sink_credential.get("credentialType")
        if ctype == "PRIVATE_KEY_JWT":
            raise errors.CamaraError(422, "PRIVATE_KEY_JWT_NOT_CONFIGURED",
                                     "PRIVATE_KEY_JWT sink credentials are not supported.")
        if ctype != "ACCESSTOKEN" or not sink_credential.get("accessToken"):
            raise errors.CamaraError(400, "INVALID_CREDENTIAL", "Only ACCESSTOKEN credentials with accessToken are supported.")


class Notifier:
    def __init__(self, cfg: Notify, source: str, transport: httpx.AsyncBaseTransport | None = None):
        self.cfg = cfg
        self.source = source
        self._client = httpx.AsyncClient(timeout=cfg.timeout, transport=transport)
        self._tasks: set[asyncio.Task] = set()
        self.sent: list[dict] = []       # last events, for tests / lab inspection

    async def aclose(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        await self._client.aclose()

    def event(self, etype: str, data: dict) -> dict:
        return {"id": str(uuid.uuid4()), "source": self.source, "type": etype, "specversion": "1.0",
                "datacontenttype": "application/json", "time": now_iso(), "data": data}

    def send(self, sink: str | None, sink_credential: dict | None, etype: str, data: dict) -> dict | None:
        """Schedule delivery; returns the event (or None if there is no sink)."""
        if not sink:
            return None
        ev = self.event(etype, data)
        self.sent = (self.sent + [ev])[-50:]
        t = asyncio.create_task(self._deliver(sink, sink_credential, ev))
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return ev

    async def _deliver(self, sink: str, cred: dict | None, ev: dict) -> None:
        headers = {"content-type": "application/cloudevents+json", "x-correlator": ev["id"]}
        if cred and cred.get("credentialType") == "ACCESSTOKEN":
            headers["authorization"] = f"Bearer {cred['accessToken']}"
        delay = 0.5
        for attempt in range(1, self.cfg.retries + 1):
            try:
                r = await self._client.post(sink, json=ev, headers=headers)
                if r.status_code < 300:
                    log.info("event %s %s -> %s (%d)", ev["type"], ev["id"], sink, r.status_code)
                    return
                if r.status_code == 410:     # SinkGone: stop retrying
                    log.warning("sink gone %s", sink)
                    return
                log.warning("event %s -> %s: HTTP %d (attempt %d)", ev["id"], sink, r.status_code, attempt)
            except httpx.HTTPError as exc:
                log.warning("event %s -> %s: %s (attempt %d)", ev["id"], sink, exc, attempt)
            await asyncio.sleep(delay)
            delay *= 2
        log.error("event %s to %s dropped after %d attempts", ev["id"], sink, self.cfg.retries)
