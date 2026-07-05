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


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window, per-client-IP rate limit. In-memory (single-process).

    A generous cap that lets normal browsing through while blocking abusive bursts of
    the scan-heavy read endpoints. ``limit <= 0`` disables it entirely.
    """

    # Sweep expired entries once the map grows past this, so a churn of distinct client
    # IPs can't grow it without bound (in-memory, single process).
    _SWEEP_THRESHOLD = 10_000

    def __init__(self, app: object, *, limit: int, window_sec: float = 60.0) -> None:
        super().__init__(app)  # pyright: ignore[reportArgumentType]
        self._limit = limit
        self._window = window_sec
        self._hits: dict[str, tuple[float, int]] = {}

    def _sweep_expired(self, now: float) -> None:
        expired = [
            key for key, (start, _) in self._hits.items() if now - start >= self._window
        ]
        for key in expired:
            del self._hits[key]

    async def dispatch(self, request: Request, call_next: Handler) -> Response:
        if self._limit <= 0:
            return await call_next(request)
        key = request.client.host if request.client else "unknown"
        now = time.monotonic()
        if len(self._hits) > self._SWEEP_THRESHOLD:
            self._sweep_expired(now)
        window_start, count = self._hits.get(key, (now, 0))
        if now - window_start >= self._window:
            window_start, count = now, 0
        count += 1
        self._hits[key] = (window_start, count)
        if count > self._limit:
            retry_after = max(1, int(self._window - (now - window_start)))
            request_id = getattr(request.state, "request_id", None)
            response: Response = JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded. Slow down.",
                    "error": "rate_limited",
                    "request_id": request_id,
                },
                headers={"Retry-After": str(retry_after)},
            )
            _apply_hardening_headers(response, request_id)
            return response
        return await call_next(request)


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
