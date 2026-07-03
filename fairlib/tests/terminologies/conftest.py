"""Fixtures for terminology-store tests.

The integration tests run against the real running Oxigraph NCIt store (no mocks).
``ncit_url`` skips the test cleanly when that store is not reachable, so CI (which has
no store) and offline dev stay green while local runs exercise the real endpoint.
"""

import os

import httpx
import pytest

_DEFAULT_NCIT_URL = "http://localhost:7878"


def _reachable(url: str) -> bool:
    try:
        resp = httpx.post(
            f"{url.rstrip('/')}/query",
            content=b"ASK {}",
            headers={
                "Content-Type": "application/sparql-query",
                "Accept": "application/sparql-results+json",
            },
            timeout=2.0,
        )
    except httpx.HTTPError:
        return False
    return resp.status_code == 200


@pytest.fixture
def ncit_url() -> str:
    """Base URL of the live NCIt Oxigraph store; skip if it is not reachable."""
    url = os.environ.get("NCIT_SPARQL_URL", _DEFAULT_NCIT_URL)
    if not _reachable(url):
        pytest.skip(f"NCIt Oxigraph not reachable at {url}")
    return url
