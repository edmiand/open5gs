"""FastAPI application factory for the open5gs-camara gateway."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import errors
from .auth.tokens import Principal, require_scopes
from .auth.tokens import router as auth_router
from .config import Config, load_config
from .core.info import InfoClient, InfoUnavailable
from .core.mongo import Store
from .core.resolver import Resolver
from .core.sbi import SbiClient
from .models import Device

VERSION = "0.1.0"


# ── health ───────────────────────────────────────────────────────────────────
health_router = APIRouter(tags=["ops"])


async def _check(coro) -> dict:
    try:
        detail = await coro
        return {"ok": True, "detail": detail}
    except Exception as exc:  # report, never raise, from health
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


async def _nrf_registered(sbi: SbiClient, nf_type: str) -> str:
    inst = [p for p in await sbi.nrf_profiles(nf_type) if p.get("nfStatus") == "REGISTERED"]
    if not inst:
        raise RuntimeError(f"no REGISTERED {nf_type} in NRF")
    return f"{len(inst)} registered"


@health_router.get("/health")
async def health(request: Request):
    st = request.app.state

    async def mongo():
        await asyncio.to_thread(st.store.ping)
        return "ping ok"

    async def smf():
        return f"{len(await st.info.pdu_info())} UE(s) with PDU sessions"

    async def amf():
        return f"{len(await st.info.ue_info())} UE(s) known to AMF"

    names = ["mongo", "nrf:BSF", "nrf:PCF", "nrf:UDM", "smf:pdu-info", "amf:ue-info"]
    results = await asyncio.gather(
        _check(mongo()), _check(_nrf_registered(st.sbi, "BSF")), _check(_nrf_registered(st.sbi, "PCF")),
        _check(_nrf_registered(st.sbi, "UDM")), _check(smf()), _check(amf()))
    checks = dict(zip(names, results))
    up = all(c["ok"] for c in checks.values())
    return JSONResponse({"status": "UP" if up else "DOWN", "version": VERSION, "checks": checks},
                        status_code=200 if up else 503)


# ── lab helpers ──────────────────────────────────────────────────────────────
lab_router = APIRouter(prefix="/_lab", tags=["lab"])


class ResolveRequest(BaseModel):
    device: Device | None = None


@lab_router.post("/resolve")
async def lab_resolve(body: ResolveRequest, request: Request,
                      _: Principal = Depends(require_scopes("lab:read"))):
    """Resolve a CAMARA Device to SUPI / phone / UE IPs (Phase 0 resolver check)."""
    rd = await request.app.state.resolver.resolve(body.device)
    out = asdict(rd)
    out["ipv4Addresses"] = rd.ipv4_addresses
    return out


# ── factory ──────────────────────────────────────────────────────────────────
def create_app(cfg: Config | None = None, *, store: Store | None = None, sbi: SbiClient | None = None,
               info: InfoClient | None = None) -> FastAPI:
    cfg = cfg or load_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.config = cfg
        app.state.store = store or Store.connect(cfg.mongo)
        app.state.sbi = sbi or SbiClient(cfg.sbi)
        app.state.info = info or InfoClient(cfg.info)
        app.state.resolver = Resolver(app.state.store, app.state.info)
        try:
            yield
        finally:
            await app.state.sbi.aclose()
            await app.state.info.aclose()

    app = FastAPI(title="open5gs-camara", version=VERSION, lifespan=lifespan)
    errors.install(app)
    app.include_router(health_router)
    app.include_router(auth_router)
    if cfg.lab_endpoints:
        app.include_router(lab_router)
    return app
