"""Cross-cutting HTTP middleware: request-id correlation, security headers, logging.

Kept deliberately small — the reusable slice of a production API surface, without the
fairdata-specific etag/idempotency machinery. Errors are shaped into a consistent
envelope by :func:`install_error_handlers`.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from ontolib.core.logging_config import get_logger

if TYPE_CHECKING:
    from starlette.responses import Response

logger = get_logger(__name__)

_REQUEST_ID_HEADER = "X-Request-ID"
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}

Handler = Callable[[Request], Awaitable["Response"]]


def _apply_hardening_headers(response: Response, request_id: str | None) -> None:
    """Add the request-id and security headers to *response* (idempotent)."""
    if request_id is not None:
        response.headers[_REQUEST_ID_HEADER] = request_id
    for name, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign/propagate a request id, add security headers, and log each request."""

    async def dispatch(self, request: Request, call_next: Handler) -> Response:
        request_id = request.headers.get(_REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.state.request_id = request_id
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
        _apply_hardening_headers(response, request_id)
        logger.info(
            "%s %s -> %d (%.1fms) rid=%s",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
            request_id,
        )
        return response


def install_error_handlers(app: FastAPI) -> None:
    """Shape errors into ``{detail, error, request_id}`` (``detail`` kept for clients).

    ``detail`` preserves FastAPI's default field so existing clients keep working.
    """

    def _request_id(request: Request) -> str | None:
        return getattr(request.state, "request_id", None)

    @app.exception_handler(HTTPException)
    async def _http_exc(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": exc.detail,
                "error": "http_error",
                "request_id": _request_id(request),
            },
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        request_id = _request_id(request)
        logger.exception("unhandled error rid=%s: %s", request_id, exc)
        # This handler runs in Starlette's outer ServerErrorMiddleware, above
        # RequestContextMiddleware — so a 500 would otherwise ship without the
        # request-id / security headers. Apply them here to keep them consistent.
        response = JSONResponse(
            status_code=500,
            content={
                "detail": "Internal server error",
                "error": "internal_error",
                "request_id": request_id,
            },
        )
        _apply_hardening_headers(response, request_id)
        return response
