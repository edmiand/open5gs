"""CAMARA Commonalities error model and x-correlator handling.

Errors are returned as ErrorInfo: {"status": int, "code": str, "message": str}.
"""

from __future__ import annotations

import re
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

X_CORRELATOR = "x-correlator"
_X_CORRELATOR_RE = re.compile(r"^[a-zA-Z0-9\-_:;./<>{}]{0,256}$")


class CamaraError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


# Shorthands for the common Commonalities codes
def invalid_argument(msg: str) -> CamaraError:
    return CamaraError(400, "INVALID_ARGUMENT", msg)


def unauthenticated(msg: str = "Request not authenticated due to missing, invalid, or expired credentials.") -> CamaraError:
    return CamaraError(401, "UNAUTHENTICATED", msg)


def permission_denied(msg: str = "Client does not have sufficient permissions to perform this action.") -> CamaraError:
    return CamaraError(403, "PERMISSION_DENIED", msg)


def identifier_not_found(msg: str = "Device identifier not found.") -> CamaraError:
    return CamaraError(404, "IDENTIFIER_NOT_FOUND", msg)


def missing_identifier(msg: str = "The device cannot be identified.") -> CamaraError:
    return CamaraError(422, "MISSING_IDENTIFIER", msg)


def unsupported_identifier(msg: str = "The identifier provided is not supported.") -> CamaraError:
    return CamaraError(422, "UNSUPPORTED_IDENTIFIER", msg)


def identifier_mismatch(msg: str = "Provided identifiers are not consistent.") -> CamaraError:
    return CamaraError(422, "IDENTIFIER_MISMATCH", msg)


def service_not_applicable(msg: str = "The service is not available for the provided identifier.") -> CamaraError:
    return CamaraError(422, "SERVICE_NOT_APPLICABLE", msg)


def unavailable(msg: str = "Service unavailable.") -> CamaraError:
    return CamaraError(503, "UNAVAILABLE", msg)


def _body(status: int, code: str, message: str) -> dict:
    return {"status": status, "code": code, "message": message}


_HTTP_DEFAULT_CODES = {
    400: "INVALID_ARGUMENT", 401: "UNAUTHENTICATED", 403: "PERMISSION_DENIED",
    404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 406: "NOT_ACCEPTABLE",
    409: "CONFLICT", 415: "UNSUPPORTED_MEDIA_TYPE", 429: "TOO_MANY_REQUESTS",
    500: "INTERNAL", 503: "UNAVAILABLE",
}


def install(app: FastAPI) -> None:
    """Register x-correlator middleware and CAMARA-shaped exception handlers."""

    @app.middleware("http")
    async def correlator(request: Request, call_next):
        corr = request.headers.get(X_CORRELATOR)
        if corr is not None and not _X_CORRELATOR_RE.match(corr):
            resp = JSONResponse(_body(400, "INVALID_ARGUMENT", "Invalid x-correlator header."), status_code=400)
            return resp
        request.state.correlator = corr or str(uuid.uuid4())
        resp = await call_next(request)
        resp.headers[X_CORRELATOR] = request.state.correlator
        return resp

    @app.exception_handler(CamaraError)
    async def camara_error(_: Request, exc: CamaraError):
        return JSONResponse(_body(exc.status, exc.code, exc.message), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", []) if p != "body")
        msg = f"{loc}: {first.get('msg', 'invalid request')}" if loc else first.get("msg", "invalid request")
        return JSONResponse(_body(400, "INVALID_ARGUMENT", msg), status_code=400)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException):
        code = _HTTP_DEFAULT_CODES.get(exc.status_code, "INTERNAL")
        return JSONResponse(_body(exc.status_code, code, str(exc.detail)), status_code=exc.status_code,
                            headers=getattr(exc, "headers", None))
