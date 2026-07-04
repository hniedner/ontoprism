"""Authorization for the mutating endpoints.

When ``api_key`` is configured, callers of the refresh/reload endpoints must present a
matching ``X-API-Key`` header. When it is unset (the dev default), the endpoints stay
open so local development needs no secret.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from backend.config import get_settings


def require_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    """Reject the request unless the configured API key matches ``X-API-Key``.

    A no-op when no ``api_key`` is configured (open dev mode).
    """
    expected = get_settings().api_key
    if expected is None:
        return
    if x_api_key != expected:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Missing or invalid API key.",
            headers={"WWW-Authenticate": "API-Key"},
        )


RequireApiKey = Depends(require_api_key)
