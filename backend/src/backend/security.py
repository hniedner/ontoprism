"""Authorization for mutating endpoints and protected ICD-O repository reads.

When ``api_key`` is configured, callers of the refresh/reload endpoints must present a
matching ``X-API-Key`` header. When it is unset (the dev default), the endpoints stay
open so local development needs no secret.
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from backend.config import get_settings


def require_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    """Reject the request unless the configured API key matches ``X-API-Key``.

    A no-op when no ``api_key`` is configured — an unset *or empty* key means open dev
    mode (an empty secret must not lock the endpoints behind an empty string). The
    comparison is constant-time to avoid leaking the key via timing.
    """
    expected = get_settings().api_key
    if not expected:
        return
    if not secrets.compare_digest(x_api_key or "", expected):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Missing or invalid API key.",
            headers={"WWW-Authenticate": "API-Key"},
        )


RequireApiKey = Depends(require_api_key)


def require_icdo_entitlement(
    x_icdo_entitlement: Annotated[str | None, Header()] = None,
) -> None:
    """Refuse protected ICD-O access before any repository dependency is resolved."""
    expected = get_settings().icdo_entitlement_key
    if not expected or not secrets.compare_digest(x_icdo_entitlement or "", expected):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "ICD-O entitlement required.")


def has_icdo_entitlement(value: str | None) -> bool:
    """Return whether a supplied entitlement matches the configured consumer key."""
    expected = get_settings().icdo_entitlement_key
    return bool(expected and secrets.compare_digest(value or "", expected))


RequireIcdoEntitlement = Depends(require_icdo_entitlement)
