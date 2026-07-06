"""Integration tests for the NCIt FTS search cache (populate from store → search)."""

from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient


@pytest.mark.integration
def test_populate_search_index_then_search_from_cache(
    live_api_client: TestClient,
) -> None:
    # Rebuild the cache from the live store (the seeded fixture in CI).
    built = live_api_client.post("/api/v1/refresh/ncit/search-index")
    if built.status_code == HTTPStatus.BAD_GATEWAY:
        pytest.skip("NCIt store or Postgres unavailable")
    assert built.status_code == HTTPStatus.OK
    assert built.json()["concepts_indexed"] >= 1

    # Search is now served from the cache and returns the neoplasm concepts.
    resp = live_api_client.get("/api/v1/ncit/search", params={"q": "neoplasm"})
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert body["total"] >= 1
    # C3262 (Neoplasm) is a deterministic match in the seeded fixture.
    assert "C3262" in {hit["code"] for hit in body["hits"]}
