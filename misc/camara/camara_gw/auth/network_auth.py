"""Network-based authentication (CAMARA ICM authorization-code flow, no user interaction).

GET /oauth2/authorize must be reached over the device's mobile data path (UE -> UPF -> ogstun ->
gateway at 10.45.0.1). The request's source IP is the UE address of a live PDU session, which
SMF /pdu-info maps to the SUPI: that is the network-based authentication. Anything else
(off-network, unknown IP) gets an `access_denied` redirect; there's never a login page.

POST /oauth2/token grant_type=authorization_code exchanges the single-use code (PKCE S256 optional)
for a short-lived access token (sub = SUPI, amr = ["network"]) and an OIDC id_token.
No refresh tokens. Number Verification access tokens are single-use (see consume_token).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import jwt
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pymongo.errors import DuplicateKeyError

from .. import errors
from ..models import Device, DeviceIpv4Addr
from .tokens import ALG, Principal, THREE_LEGGED_ONLY, _oauth_error, issue_token

log = logging.getLogger("camara.nba")

router = APIRouter(tags=["auth"])
CODES = "camara_auth_codes"
USED = "camara_used_tokens"
AMR_NETWORK = "network"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _redirect(redirect_uri: str, **params) -> RedirectResponse:
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{sep}{urlencode({k: v for k, v in params.items() if v is not None})}",
                            status_code=302)


async def ensure_indexes(db) -> None:
    def mk():
        db[CODES].create_index("expireAt", expireAfterSeconds=0)
        db[USED].create_index("expireAt", expireAfterSeconds=0)
    await asyncio.to_thread(mk)


@router.get("/oauth2/authorize")
async def authorize(request: Request,
                    response_type: str = Query(...), client_id: str = Query(...),
                    redirect_uri: str = Query(...), scope: str = Query(...),
                    state: str | None = Query(None), nonce: str | None = Query(None),
                    code_challenge: str | None = Query(None), code_challenge_method: str | None = Query(None),
                    prompt: str | None = Query(None)):
    st = request.app.state
    cfg = st.config
    client = next((c for c in cfg.auth.clients if c.client_id == client_id), None)
    # Never redirect to an unregistered URI: answer directly instead.
    if client is None or redirect_uri not in client.redirect_uris:
        raise _oauth_error(400, "invalid_request", "Unknown client_id or unregistered redirect_uri.")
    if response_type != "code":
        return _redirect(redirect_uri, error="unsupported_response_type", state=state)
    scopes = scope.split()
    if "openid" not in scopes:
        return _redirect(redirect_uri, error="invalid_scope", error_description="openid scope required", state=state)
    if set(scopes) - set(client.scopes):
        return _redirect(redirect_uri, error="invalid_scope", state=state,
                         error_description="scope not allowed for client")
    if code_challenge and code_challenge_method not in (None, "S256"):
        return _redirect(redirect_uri, error="invalid_request", error_description="only S256 PKCE", state=state)

    # ── network-based authentication: source IP -> live PDU session -> SUPI ──
    ip = request.client.host if request.client else ""
    supi = None
    try:
        if ipaddress.ip_address(ip).version == 4:
            rd = await st.resolver.resolve(Device(ipv4Address=DeviceIpv4Addr(privateAddress=ip)))
            supi = rd.supi
    except (ValueError, errors.CamaraError):
        supi = None
    if supi is None:
        log.info("authorize: %s not identified by the mobile network (client %s)", ip, client_id)
        # prompt=none or not: there is no interactive fallback for network-based authentication
        return _redirect(redirect_uri, error="access_denied", state=state,
                         error_description="Device not identified by the mobile network")

    code = secrets.token_urlsafe(32)
    doc = {"_id": code, "client_id": client_id, "redirect_uri": redirect_uri, "scopes": scopes,
           "supi": supi, "ip": ip, "nonce": nonce, "code_challenge": code_challenge,
           "expireAt": _now() + timedelta(seconds=cfg.auth.code_ttl)}
    await asyncio.to_thread(st.store.db[CODES].insert_one, doc)
    log.info("authorize: %s -> %s (client %s, scopes %s)", ip, supi, client_id, scope)
    return _redirect(redirect_uri, code=code, state=state)


async def exchange_code(request: Request, client, code: str | None, redirect_uri: str | None,
                        code_verifier: str | None):
    st = request.app.state
    cfg = st.config
    if not code or not redirect_uri:
        raise _oauth_error(400, "invalid_request", "code and redirect_uri are required.")
    # single use: delete on read
    doc = await asyncio.to_thread(st.store.db[CODES].find_one_and_delete, {"_id": code})
    exp = doc and doc["expireAt"]
    if exp is not None and exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if not doc or exp <= _now() or doc["client_id"] != client.client_id or doc["redirect_uri"] != redirect_uri:
        raise _oauth_error(400, "invalid_grant", "Invalid, expired or already used authorization code.")
    if doc.get("code_challenge"):
        if not code_verifier:
            raise _oauth_error(400, "invalid_grant", "code_verifier required.")
        calc = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).rstrip(b"=").decode()
        if calc != doc["code_challenge"]:
            raise _oauth_error(400, "invalid_grant", "PKCE verification failed.")

    scopes = doc["scopes"]
    nv = bool(set(scopes) & (THREE_LEGGED_ONLY - {"openid"}))
    ttl = cfg.auth.nv_token_ttl if nv else cfg.auth.token_ttl
    out = issue_token(cfg, client.client_id, scopes, sub=doc["supi"], ttl=ttl,
                      extra={"amr": [AMR_NETWORK], "auth_time": int(time.time())})
    now = int(time.time())
    id_claims = {"iss": cfg.auth.issuer, "sub": doc["supi"], "aud": client.client_id, "iat": now,
                 "exp": now + ttl, "auth_time": now, "amr": [AMR_NETWORK]}
    if doc.get("nonce"):
        id_claims["nonce"] = doc["nonce"]
    out["id_token"] = jwt.encode(id_claims, cfg.auth.signing_key, algorithm=ALG)
    return JSONResponse(out, headers={"cache-control": "no-store"})


async def consume_token(request: Request, p: Principal) -> None:
    """Enforce single use of a token (jti). Second use -> 401."""
    claims = p.claims or {}
    jti = claims.get("jti")
    if not jti:
        raise errors.unauthenticated()
    exp = datetime.fromtimestamp(claims.get("exp", time.time()), timezone.utc)
    try:
        await asyncio.to_thread(request.app.state.store.db[USED].insert_one, {"_id": jti, "expireAt": exp})
    except DuplicateKeyError:
        raise errors.unauthenticated("Access token already used (single-use token).")


@router.get("/.well-known/openid-configuration")
async def discovery(request: Request):
    base = str(request.base_url).rstrip("/")
    cfg = request.app.state.config
    return {
        "issuer": cfg.auth.issuer,
        "authorization_endpoint": f"{base}/oauth2/authorize",
        "token_endpoint": f"{base}/oauth2/token",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "client_credentials"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": [ALG],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
        "scopes_supported": sorted({s for c in cfg.auth.clients for s in c.scopes}),
        "claims_supported": ["sub", "amr", "auth_time", "nonce"],
    }
