"""Minimal OAuth2 token issuer + bearer verification (lab use).

- POST /oauth2/token, grant_type=client_credentials (2-legged CAMARA calls)
- `require_scopes(...)` FastAPI dependency checks the bearer JWT and its scopes.

The network-based /authorize flow for Number Verification is added in Phase 3.
"""

from __future__ import annotations

import base64
import hmac
import time
import uuid
from dataclasses import dataclass

import jwt
from fastapi import APIRouter, Depends, Form, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..config import Config
from ..errors import CamaraError, permission_denied, unauthenticated

ALG = "HS256"
AUDIENCE = "open5gs-camara"

router = APIRouter(tags=["auth"])
_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    client_id: str
    scopes: frozenset[str]
    sub: str | None = None          # device subject (SUPI) for 3-legged tokens; None for 2-legged
    claims: dict | None = None


def _cfg(request: Request) -> Config:
    return request.app.state.config


def issue_token(cfg: Config, client_id: str, scopes: list[str], sub: str | None = None,
                extra: dict | None = None) -> dict:
    now = int(time.time())
    claims = {
        "iss": cfg.auth.issuer, "aud": AUDIENCE, "iat": now, "exp": now + cfg.auth.token_ttl,
        "jti": str(uuid.uuid4()), "client_id": client_id, "scope": " ".join(scopes),
        "sub": sub or client_id,
    }
    if sub:
        claims["device_sub"] = sub
    if extra:
        claims.update(extra)
    token = jwt.encode(claims, cfg.auth.signing_key, algorithm=ALG)
    return {"access_token": token, "token_type": "Bearer", "expires_in": cfg.auth.token_ttl,
            "scope": claims["scope"]}


def _basic_creds(request: Request) -> tuple[str, str] | None:
    h = request.headers.get("authorization", "")
    if not h.lower().startswith("basic "):
        return None
    try:
        cid, _, sec = base64.b64decode(h[6:]).decode().partition(":")
        return cid, sec
    except Exception:
        return None


def _oauth_error(status: int, error: str, desc: str) -> CamaraError:
    # RFC 6749 errors are reported via the CAMARA error body (code = OAuth error name)
    return CamaraError(status, error, desc)


@router.post("/oauth2/token")
async def token(
    request: Request,
    grant_type: str = Form(...),
    scope: str | None = Form(None),
    client_id: str | None = Form(None),
    client_secret: str | None = Form(None),
):
    cfg = _cfg(request)
    if grant_type != "client_credentials":
        raise _oauth_error(400, "unsupported_grant_type", "Only client_credentials is supported here.")
    creds = _basic_creds(request) or (client_id, client_secret)
    if not creds[0] or creds[1] is None:
        raise _oauth_error(401, "invalid_client", "Client authentication required.")
    client = next((c for c in cfg.auth.clients if c.client_id == creds[0]), None)
    if client is None or not hmac.compare_digest(client.client_secret.encode(), creds[1].encode()):
        raise _oauth_error(401, "invalid_client", "Unknown client or bad secret.")
    requested = scope.split() if scope else list(client.scopes)
    not_allowed = sorted(set(requested) - set(client.scopes))
    if not_allowed:
        raise _oauth_error(400, "invalid_scope", f"Scope(s) not allowed for client: {' '.join(not_allowed)}")
    return issue_token(cfg, client.client_id, requested)


def decode(cfg: Config, token: str) -> dict:
    return jwt.decode(token, cfg.auth.signing_key, algorithms=[ALG], audience=AUDIENCE,
                      issuer=cfg.auth.issuer, options={"require": ["exp", "iat", "iss", "aud"]})


def _principal(request: Request, creds: HTTPAuthorizationCredentials | None) -> Principal:
    if creds is None or creds.scheme.lower() != "bearer":
        raise unauthenticated()
    try:
        claims = decode(_cfg(request), creds.credentials)
    except jwt.PyJWTError:
        raise unauthenticated()
    granted = frozenset((claims.get("scope") or "").split())
    return Principal(client_id=claims.get("client_id", claims.get("sub")), scopes=granted,
                     sub=claims.get("device_sub"), claims=claims)


def require_scopes(*scopes: str):
    """Dependency: valid bearer JWT carrying all `scopes`."""

    async def dep(request: Request,
                  creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> Principal:
        p = _principal(request, creds)
        if not set(scopes) <= p.scopes:
            raise permission_denied()
        return p

    return dep


def require_any_scope(*scopes: str):
    """Dependency: valid bearer JWT carrying at least one of `scopes` (OpenAPI security alternatives)."""

    async def dep(request: Request,
                  creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> Principal:
        p = _principal(request, creds)
        if not set(scopes) & p.scopes:
            raise permission_denied()
        return p

    return dep
