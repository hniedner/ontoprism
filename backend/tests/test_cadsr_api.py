"""caDSR API endpoints against a real temp SQLite DB (no mocks, CI-runnable)."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.dependencies import (
    get_cadsr_repo,
    get_embedding_store,
    get_repository_metadata,
)
from backend.repository_metadata import RepositoryUnhealthy


@pytest.mark.api
def test_cde_detail_renders_concepts_and_pvs(cadsr_client: TestClient) -> None:
    resp = cadsr_client.get("/api/v1/cadsr/cdes/100")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["short_name"] == "NEOPLASM_HIST"
    assert body["permissible_values"][0]["value"] == "Carcinoma"
    assert any(c["concept_code"] == "C3262" for c in body["concepts"])


@pytest.mark.api
def test_unknown_cde_is_404(cadsr_client: TestClient) -> None:
    response = cadsr_client.get("/api/v1/cadsr/cdes/999999")
    assert response.status_code == 404
    assert response.json()["detail"] == "CDE not found: 999999"


@pytest.mark.api
def test_search_returns_hits(cadsr_client: TestClient) -> None:
    body = cadsr_client.get("/api/v1/cadsr/search", params={"q": "neoplasm"}).json()
    assert body["total"] == 1
    assert body["hits"][0]["public_id"] == "100"


@pytest.mark.api
def test_list_browses_without_a_query(cadsr_client: TestClient) -> None:
    # The no-search browse endpoint defaults to deterministic source order;
    # no `q` is needed.
    resp = cadsr_client.get("/api/v1/cadsr/list", params={"limit": 10})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["query"] == ""
    assert body["total"] >= 1
    assert body["hits"][0]["public_id"] == "100"


@pytest.mark.api
def test_grid_filters_are_validated_applied_and_echoed(
    cadsr_client: TestClient,
) -> None:
    response = cadsr_client.get(
        "/api/v1/cadsr/list",
        params={
            "workflow_status": "RELEASED",
            "name_text": "histology",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["filters"] == {"workflow_status": ["RELEASED"]}
    assert body["column_text"] == {"name": "histology"}
    assert body["hits"][0]["workflow_status"] == "RELEASED"


@pytest.mark.api
def test_filter_domains_preserve_source_values(cadsr_client: TestClient) -> None:
    response = cadsr_client.get("/api/v1/cadsr/filter-domains")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "value_domain_type": ["Enumerated"],
        "workflow_status": ["RELEASED"],
        "registration_status": ["Standard"],
        "context": ["caDSR"],
        "datatype": ["CHARACTER"],
    }


@pytest.mark.api
@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/cadsr/list",
        "/api/v1/cadsr/search?q=neoplasm",
        "/api/v1/cadsr/cdes/100",
        "/api/v1/cadsr/cdes/100/similar",
        "/api/v1/cadsr/filter-domains",
    ],
)
def test_primary_reads_require_certification(
    cadsr_client: TestClient, path: str
) -> None:
    class UnreadableRepository:
        calls = 0

        def __getattr__(self, _name: str):
            def fail(*_args, **_kwargs) -> None:
                self.calls += 1
                raise AssertionError("uncertified repository read")

            return fail

    class UnhealthyMetadata:
        @staticmethod
        async def cadsr(*, force: bool = False) -> RepositoryUnhealthy:
            del force
            return RepositoryUnhealthy(
                repository="cadsr",
                reason="repository-unreachable",
                message="caDSR source drifted",
            )

    app = cadsr_client.app
    assert isinstance(app, FastAPI)
    repository = UnreadableRepository()
    app.dependency_overrides[get_cadsr_repo] = lambda: repository
    app.dependency_overrides[get_embedding_store] = object
    app.dependency_overrides[get_repository_metadata] = UnhealthyMetadata

    response = cadsr_client.get(path)

    assert response.status_code == 503
    assert response.json()["detail"]["reason"] == "repository-unreachable"
    assert repository.calls == 0


@pytest.mark.api
@pytest.mark.parametrize("path", ["list", "search?q=neoplasm"])
def test_cadsr_rejects_removed_relevance_sort(
    cadsr_client: TestClient, path: str
) -> None:
    separator = "&" if "?" in path else "?"
    response = cadsr_client.get(f"/api/v1/cadsr/{path}{separator}sort=relevance")

    assert response.status_code == 422


@pytest.mark.api
def test_cdes_for_concept_join(cadsr_client: TestClient) -> None:
    # The caDSR<->NCIt cross-link: CDEs mapped to NCIt concept C3262.
    resp = cadsr_client.get("/api/v1/cadsr/concepts/C3262/cdes")
    assert resp.status_code == 200
    assert [c["public_id"] for c in resp.json()] == ["100"]
