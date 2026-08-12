"""Integration tests for the NCIt FTS search cache (populate from store → search)."""

from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

pytestmark = [
    pytest.mark.mutating_integration,
    pytest.mark.usefixtures("isolated_postgres_settings", "isolated_qlever_settings"),
]


@pytest.mark.integration
def test_populate_search_index_then_search_from_cache(
    isolated_api_client: TestClient,
) -> None:
    # Rebuild the cache from the bounded disposable QLever fixture.
    built = isolated_api_client.post("/api/v1/refresh/ncit/search-index")
    assert built.status_code == HTTPStatus.OK, built.text
    assert 1 <= built.json()["concepts_indexed"] <= 11

    # Search is now served from the cache and returns the neoplasm concepts.
    resp = isolated_api_client.get("/api/v1/ncit/search", params={"q": "neoplasm"})
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert body["total"] >= 1
    # C3262 (Neoplasm) is a deterministic match in the seeded fixture.
    assert "C3262" in {hit["code"] for hit in body["hits"]}


@pytest.mark.integration
def test_filtered_search_cache_matches_sparql_fallback(
    isolated_api_client: TestClient,
) -> None:
    params = {
        "q": "neoplasm",
        "limit": 5,
        "offset": 0,
        "representation_status": "legacy-precoordinated",
    }

    fallback = isolated_api_client.get("/api/v1/ncit/search", params=params)
    assert fallback.status_code == HTTPStatus.OK, fallback.text
    assert fallback.json()["hits"]
    assert all(
        hit["representation_status"] == "legacy-precoordinated"
        for hit in fallback.json()["hits"]
    )

    built = isolated_api_client.post("/api/v1/refresh/ncit/search-index")
    assert built.status_code == HTTPStatus.OK, built.text
    cached = isolated_api_client.get("/api/v1/ncit/search", params=params)

    assert cached.status_code == HTTPStatus.OK, cached.text
    assert cached.json() == fallback.json()
