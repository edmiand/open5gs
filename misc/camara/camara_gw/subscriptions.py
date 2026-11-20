"""Generic CAMARA event-subscription manager (Commonalities subscription model).

Shared by the *-subscriptions APIs (SIM swap now; roaming status in Phase 4).
State in Mongo `camara_subscriptions`; events delivered as CloudEvents via Notifier.

Implemented: create (201, sync ACTIVE), list (paged), get, delete (204), subscriptionExpireTime,
subscriptionMaxEvents, subscription-ended lifecycle events. Only protocol HTTP, one event type
per subscription (Commonalities rule), 2-legged tokens (subject in subscriptionDetail).
"""

from __future__ import annotations

import asyncio
import logging
import math
import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import errors
from .auth.tokens import Principal, require_any_scope, require_scopes
from .notify import Notifier, validate_sink

log = logging.getLogger("camara.subs")
COLLECTION = "camara_subscriptions"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _iso(dt: datetime | None) -> str | None:
    dt = _aware(dt)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z") if dt else None


class SubscriptionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subscriptionDetail: dict
    subscriptionExpireTime: datetime | None = None
    subscriptionMaxEvents: int | None = Field(None, ge=1, le=1_000_000)
    initialEvent: bool | None = None


class SubscriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol: str
    sink: str = Field(..., max_length=2048)
    sinkCredential: dict | None = None
    types: list[str] = Field(..., min_length=1)
    config: SubscriptionConfig
    protocolSettings: dict | None = None


# Resolves subscriptionDetail -> (subject used for matching events, detail echoed back).
# Raises CamaraError for unknown/missing identifiers.
SubjectResolver = Callable[[Request, dict, Principal], Awaitable[tuple[dict, dict]]]


class SubscriptionManager:
    def __init__(self, store, notifier: Notifier):
        self.col = store.db[COLLECTION]
        self.notifier = notifier
        self._task: asyncio.Task | None = None

    # -- storage helpers --
    async def _find(self, q: dict, **kw) -> list[dict]:
        return await asyncio.to_thread(lambda: list(self.col.find(q, **kw)))

    async def _update(self, sid: str, fields: dict, inc: dict | None = None) -> None:
        upd = {"$set": fields}
        if inc:
            upd["$inc"] = inc
        await asyncio.to_thread(self.col.update_one, {"_id": sid}, upd)

    @staticmethod
    def render(doc: dict) -> dict:
        cfg = {"subscriptionDetail": doc["detail"]}
        if doc.get("expiresAt"):
            cfg["subscriptionExpireTime"] = _iso(doc["expiresAt"])
        if doc.get("maxEvents"):
            cfg["subscriptionMaxEvents"] = doc["maxEvents"]
        cred = doc.get("sinkCredential")
        if cred:      # accessToken is writeOnly
            cred = {k: v for k, v in cred.items() if k != "accessToken"}
        out = {"id": doc["_id"], "protocol": "HTTP", "sink": doc["sink"], "sinkCredential": cred or None,
               "types": doc["types"], "config": cfg,
               "startsAt": _iso(doc.get("startsAt")), "expiresAt": _iso(doc.get("expiresAt")),
               "status": doc["status"]}
        return {k: v for k, v in out.items() if v is not None}

    def _etype(self, doc: dict, name: str) -> str:
        return f"org.camaraproject.{doc['api']}.{doc['event_version']}.{name}"

    async def end(self, doc: dict, status: str, reason: str, description: str | None = None) -> None:
        await self._update(doc["_id"], {"status": status, "endedAt": _now(), "terminationReason": reason})
        data = {"subscriptionId": doc["_id"], "terminationReason": reason}
        if description:
            data["terminationDescription"] = description
        self.notifier.send(doc["sink"], doc.get("sinkCredential"), self._etype(doc, "subscription-ended"), data)
        log.info("subscription %s ended: %s", doc["_id"], reason)

    # -- event fan-out --
    async def emit(self, api: str, etype: str, subject: dict, data: dict) -> int:
        """Deliver `etype` to every ACTIVE subscription of `api` whose subject matches. Returns count."""
        q = {"api": api, "status": "ACTIVE", "types": etype, **{f"subject.{k}": v for k, v in subject.items()}}
        n = 0
        for doc in await self._find(q):
            payload = dict(data)
            if "device" in (doc.get("detail") or {}):       # echo the subscribed device identifier
                payload = {"device": doc["detail"]["device"], **payload}
            self.notifier.send(doc["sink"], doc.get("sinkCredential"), etype,
                               {**payload, "subscriptionId": doc["_id"]})
            n += 1
            sent = doc.get("eventsSent", 0) + 1
            await self._update(doc["_id"], {}, inc={"eventsSent": 1})
            if doc.get("maxEvents") and sent >= doc["maxEvents"]:
                await self.end(doc, "EXPIRED", "MAX_EVENTS_REACHED")
        return n

    # -- expiry --
    async def run_expiry(self, interval: float = 1.0) -> None:
        while True:
            try:
                for doc in await self._find({"status": "ACTIVE", "expiresAt": {"$lte": _now()}}):
                    await self.end(doc, "EXPIRED", "SUBSCRIPTION_EXPIRED")
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("subscription expiry loop error")
            await asyncio.sleep(interval)

    def start(self) -> None:
        self._task = asyncio.create_task(self.run_expiry())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()


OnCreated = Callable[[Request, dict], Awaitable[None]]


def make_subscription_router(*, api: str, version: str, event_version: str, event_names: list[str],
                             resolve_subject: SubjectResolver, on_created: OnCreated | None = None) -> APIRouter:
    """Routes for /{api}/{version}/subscriptions following the Commonalities subscription model."""
    router = APIRouter(prefix=f"/{api}/{version}", tags=[api])
    allowed = {f"org.camaraproject.{api}.{event_version}.{n}" for n in event_names}

    def mgr(request: Request) -> SubscriptionManager:
        return request.app.state.subscriptions

    @router.post("/subscriptions", status_code=201)
    async def create(body: SubscriptionRequest, request: Request,
                     p: Principal = Depends(require_any_scope(*[f"{api}:{t}:create" for t in sorted(allowed)]))):
        # order: authenticated (any create scope) -> request validity -> scope for the requested type
        if body.protocol != "HTTP":
            raise errors.CamaraError(400, "INVALID_PROTOCOL", "Only HTTP is supported.")
        bad = [t for t in body.types if t not in allowed]
        if bad:
            raise errors.invalid_argument(f"Unsupported event type {bad[0]}.")
        if len(body.types) != 1:
            raise errors.CamaraError(422, "MULTIEVENT_SUBSCRIPTION_NOT_SUPPORTED",
                                     "Multi-event subscription is not supported.")
        if f"{api}:{body.types[0]}:create" not in p.scopes:
            raise errors.CamaraError(403, "SUBSCRIPTION_MISMATCH",
                                     "The access token does not allow subscribing to the requested event type.")
        validate_sink(request.app.state.config.notify, body.sink, body.sinkCredential)
        exp = _aware(body.config.subscriptionExpireTime)
        if exp is not None and exp <= _now():
            raise errors.invalid_argument("subscriptionExpireTime is in the past.")
        subject, detail = await resolve_subject(request, body.config.subscriptionDetail, p)
        m = mgr(request)
        dup = await m._find({"api": api, "client_id": p.client_id, "status": "ACTIVE", "types": body.types[0],
                             **{f"subject.{k}": v for k, v in subject.items()}})
        if dup:
            raise errors.CamaraError(409, "ALREADY_EXISTS", f"An active subscription already exists ({dup[0]['_id']}).")
        doc = {"_id": str(uuid.uuid4()), "api": api, "event_version": event_version, "client_id": p.client_id,
               "sink": body.sink, "sinkCredential": body.sinkCredential, "types": body.types,
               "subject": subject, "detail": detail, "startsAt": _now(), "expiresAt": exp,
               "maxEvents": body.config.subscriptionMaxEvents, "eventsSent": 0, "status": "ACTIVE"}
        await asyncio.to_thread(m.col.insert_one, doc)
        log.info("subscription %s created: %s %s", doc["_id"], body.types[0], subject)
        m.notifier.send(doc["sink"], doc.get("sinkCredential"), m._etype(doc, "subscription-started"),
                        {"subscriptionId": doc["_id"], "initiationReason": "SUBSCRIPTION_CREATED"})
        if body.config.initialEvent and on_created is not None:
            await on_created(request, doc)
        return JSONResponse(SubscriptionManager.render(doc), status_code=201)

    @router.get("/subscriptions")
    async def list_subs(request: Request, page: int = Query(1, ge=1), perPage: int = Query(20, ge=1, le=100),
                        p: Principal = Depends(require_scopes(f"{api}:read"))):
        docs = await mgr(request)._find({"api": api, "client_id": p.client_id, "status": {"$in": ["ACTIVE", "INACTIVE"]}},
                                        sort=[("startsAt", 1)])
        total = len(docs)
        pages = max(1, math.ceil(total / perPage))
        chunk = docs[(page - 1) * perPage: page * perPage]
        body = {"subscriptions": [SubscriptionManager.render(d) for d in chunk],
                "pagination": {"page": page, "perPage": perPage, "totalCount": total, "totalPages": pages}}
        return JSONResponse(body, headers={"X-Total-Count": str(total), "X-Total-Pages": str(pages)})

    async def owned(request: Request, sid: str, p: Principal) -> dict:
        docs = await mgr(request)._find({"_id": sid, "api": api, "client_id": p.client_id,
                                         "status": {"$in": ["ACTIVE", "INACTIVE"]}})
        if not docs:
            raise errors.CamaraError(404, "NOT_FOUND", "The specified subscription does not exist.")
        return docs[0]

    @router.get("/subscriptions/{subscriptionId}")
    async def get_sub(subscriptionId: str, request: Request, p: Principal = Depends(require_scopes(f"{api}:read"))):
        return SubscriptionManager.render(await owned(request, subscriptionId, p))

    @router.delete("/subscriptions/{subscriptionId}", status_code=204)
    async def delete_sub(subscriptionId: str, request: Request,
                         p: Principal = Depends(require_scopes(f"{api}:delete"))):
        doc = await owned(request, subscriptionId, p)
        await mgr(request).end(doc, "DELETED", "SUBSCRIPTION_DELETED")
        return Response(status_code=204)

    return router
