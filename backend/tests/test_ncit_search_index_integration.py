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
def test_filtered_search_uses_certified_cache(
    isolated_api_client: TestClient,
) -> None:
    params = {
        "q": "neoplasm",
        "limit": 10,
        "offset": 0,
        "representation_status": "legacy-precoordinated",
    }

    built = isolated_api_client.post("/api/v1/refresh/ncit/search-index")
    assert built.status_code == HTTPStatus.OK, built.text
    cached = isolated_api_client.get("/api/v1/ncit/search", params=params)

    assert cached.status_code == HTTPStatus.OK, cached.text
    assert cached.json()["hits"]
    assert all(
        hit["representation_status"] == "legacy-precoordinated"
        for hit in cached.json()["hits"]
    )


@pytest.mark.integration
def test_ncit_column_text_and_status_filter_apply_before_pagination(
    isolated_api_client: TestClient,
) -> None:
    built = isolated_api_client.post("/api/v1/refresh/ncit/search-index")
    assert built.status_code == HTTPStatus.OK, built.text
    params = {"q": "neoplasm", "label_text": "neoplasm", "limit": 10, "offset": 0}
    unfiltered = isolated_api_client.get("/api/v1/ncit/search", params=params)
    assert unfiltered.status_code == HTTPStatus.OK, unfiltered.text
    assert unfiltered.json()["total"] >= 1
    assert all("neoplasm" in row["label"].lower() for row in unfiltered.json()["hits"])
    absent = isolated_api_client.get(
        "/api/v1/ncit/search",
        params={**params, "label_text": "no-matching-label-zqv834920"},
    )
    assert absent.status_code == HTTPStatus.OK, absent.text
    assert absent.json()["total"] == 0
    combined = isolated_api_client.get(
        "/api/v1/ncit/search",
        params={
            **params,
            "status_text": "legacy",
            "representation_status": "legacy-precoordinated",
        },
    )
    assert combined.status_code == HTTPStatus.OK, combined.text
    assert combined.json()["total"] <= unfiltered.json()["total"]
    assert all(
        row["representation_status"] == "legacy-precoordinated"
        for row in combined.json()["hits"]
    )
    assert all("neoplasm" in row["label"].lower() for row in combined.json()["hits"])
    incompatible = isolated_api_client.get(
        "/api/v1/ncit/search",
        params={
            **params,
            "status_text": "no-matching-status-zqv834920",
            "representation_status": "legacy-precoordinated",
        },
    )
    assert incompatible.status_code == HTTPStatus.OK, incompatible.text
    assert incompatible.json()["total"] == 0
    status_only = isolated_api_client.get(
        "/api/v1/ncit/search", params={"q": "neoplasm", "status_text": "legacy"}
    )
    assert status_only.status_code == HTTPStatus.OK, status_only.text
    assert status_only.json()["total"] >= combined.json()["total"]
    assert all(
        hit["representation_status"] == "legacy-precoordinated"
        for hit in status_only.json()["hits"]
    )


@pytest.mark.integration
def test_ncit_column_text_rejects_invalid_request(
    isolated_api_client: TestClient,
) -> None:
    for value in ("x" * 101, "bad\ncode"):
        response = isolated_api_client.get(
            "/api/v1/ncit/list", params={"code_text": value}
        )
        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


@pytest.mark.integration
def test_ncit_browse_text_filters_server_rows_and_total(
    isolated_api_client: TestClient,
) -> None:
    all_concepts = isolated_api_client.get("/api/v1/ncit/list", params={"limit": 10})
    assert all_concepts.status_code == HTTPStatus.OK, all_concepts.text
    filtered = isolated_api_client.get(
        "/api/v1/ncit/list", params={"code_text": "C3262", "limit": 10}
    )
    assert filtered.status_code == HTTPStatus.OK, filtered.text
    assert filtered.json()["total"] == 1
    assert [row["code"] for row in filtered.json()["hits"]] == ["C3262"]
    assert filtered.json()["total"] < all_concepts.json()["total"]
    status_only = isolated_api_client.get(
        "/api/v1/ncit/list", params={"status_text": "legacy", "limit": 10}
    )
    assert status_only.status_code == HTTPStatus.OK, status_only.text
    assert status_only.json()["total"] <= all_concepts.json()["total"]
    assert all(
        hit["representation_status"] == "legacy-precoordinated"
        for hit in status_only.json()["hits"]
    )


@pytest.mark.integration
def test_status_text_matches_displayed_label_in_list_and_search(
    isolated_api_client: TestClient,
) -> None:
    built = isolated_api_client.post("/api/v1/refresh/ncit/search-index")
    assert built.status_code == HTTPStatus.OK, built.text
    for endpoint, params in (
        ("list", {}),
        ("search", {"q": "neoplasm"}),
    ):
        response = isolated_api_client.get(
            f"/api/v1/ncit/{endpoint}",
            params={
                **params,
                "representation_status": "legacy-precoordinated",
                "status_text": "pre-coordinated",
            },
        )
        assert response.status_code == HTTPStatus.OK, response.text
        assert response.json()["total"] > 0
        assert all(
            hit["representation_status"] == "legacy-precoordinated"
            for hit in response.json()["hits"]
        )


@pytest.mark.integration
def test_ncit_search_text_treats_sql_wildcards_as_literals(
    isolated_api_client: TestClient,
) -> None:
    rebuilt = isolated_api_client.post("/api/v1/refresh/ncit/search-index")
    assert rebuilt.status_code == 200, rebuilt.text
    for value in ("%", "_", "\\"):
        response = isolated_api_client.get(
            "/api/v1/ncit/search", params={"q": "neoplasm", "label_text": value}
        )
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 0
