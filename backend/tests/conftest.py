"""Backend test fixtures."""

from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.main import create_app


def _store_reachable(url: str) -> bool:
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
def app_client() -> Iterator[TestClient]:
    """TestClient with lifespan active (client/store wired); no live store needed."""
    with TestClient(create_app()) as client:
        yield client


@pytest.fixture
def live_api_client() -> Iterator[TestClient]:
    """A TestClient wired to the live NCIt store; skips if the store is unreachable."""
    url = get_settings().ncit_sparql_url
    if not _store_reachable(url):
        pytest.skip(f"NCIt Oxigraph not reachable at {url}")
    with TestClient(create_app()) as client:
        yield client
