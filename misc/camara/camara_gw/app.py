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
from .apis import qod as qod_api
from .apis import qos_profiles as qos_profiles_api
from .apis import number_verification, sim_swap_subs
from .apis import roaming as roaming_api
from .apis import connectivity as connectivity_api
from .apis import device_identifier
from .probe import Prober
from .ratelimit import Limiter
from .roaming import RoamingMonitor
from .auth import network_auth
from .apis.swap import device_swap_router, sim_swap_router
from .subscriptions import SubscriptionManager
from .swap_history import SwapHistory
from .notify import Notifier
from .profiles import load_profiles

VERSION = "0.8.0"


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
               info: InfoClient | None = None, notifier: Notifier | None = None) -> FastAPI:
    cfg = cfg or load_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.config = cfg
        app.state.store = store or Store.connect(cfg.mongo)
        app.state.sbi = sbi or SbiClient(cfg.sbi)
        app.state.info = info or InfoClient(cfg.info)
        app.state.resolver = Resolver(app.state.store, app.state.info)
        await network_auth.ensure_indexes(app.state.store.db)
        app.state.profiles = load_profiles()
        app.state.notifier = notifier or Notifier(cfg.notify, source=f"{cfg.qod.callback_base}")
        app.state.qod = qod_api.QodService(app.state)
        app.state.qod.start()
        app.state.subscriptions = SubscriptionManager(app.state.store, app.state.notifier)
        app.state.subscriptions.start()
        app.state.swaps = SwapHistory(app.state.store, cfg.swap.poll_interval)
        app.state.swaps.listeners.append(sim_swap_subs.swap_listener(app.state.subscriptions))
        if cfg.swap.enabled:
            app.state.swaps.start()
        app.state.prober = Prober(cfg.ci)
        app.state.limiter = Limiter(cfg.rate_limit)
        app.state.roaming = RoamingMonitor(app.state.store, app.state.info, cfg.home_plmns, cfg.roaming.poll_interval)
        app.state.roaming.listeners.append(roaming_api.roaming_listener(app.state.subscriptions))
        if cfg.roaming.enabled:
            app.state.roaming.start()
        try:
            yield
        finally:
            await app.state.roaming.stop()
            await app.state.swaps.stop()
            await app.state.subscriptions.stop()
            await app.state.qod.stop()
            await app.state.notifier.aclose()
            await app.state.sbi.aclose()
            await app.state.info.aclose()

    app = FastAPI(title="open5gs-camara", version=VERSION, lifespan=lifespan)
    errors.install(app)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(qos_profiles_api.router)
    app.include_router(qod_api.router)
    app.include_router(qod_api.internal)
    app.include_router(sim_swap_router)
    app.include_router(device_swap_router)
    app.include_router(sim_swap_subs.router)
    app.include_router(network_auth.router)
    app.include_router(number_verification.router)
    app.include_router(roaming_api.router)
    app.include_router(roaming_api.subs_router)
    app.include_router(connectivity_api.profiles_router)
    app.include_router(connectivity_api.ci_router)
    app.include_router(device_identifier.router)
    if cfg.lab_endpoints:
        app.include_router(lab_router)
        app.include_router(roaming_api.lab_router)
        app.include_router(connectivity_api.lab_router)
    return app
