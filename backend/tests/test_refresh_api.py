"""Refresh endpoint tests: report (live), and reload guards (no store mutation)."""

import sqlite3
from collections.abc import AsyncIterator, Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.dependencies import (
    get_cadsr_repo,
    get_ncit_client,
    get_ncit_search_index,
    get_ncit_store,
)
from backend.main import create_app
from ontolib.core.exceptions import StorageError


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.api
def test_reload_rejects_unsupported_extension(app_client: TestClient) -> None:
    resp = app_client.post(
        "/api/v1/refresh/ncit/reload", json={"source_path": "data/some-data.csv"}
    )
    assert resp.status_code == 400
    assert "Unsupported" in resp.json()["detail"]


@pytest.mark.api
def test_reload_missing_file_is_404(app_client: TestClient) -> None:
    resp = app_client.post(
        "/api/v1/refresh/ncit/reload",
        json={"source_path": "data/missing-file-xyz-12345.ttl"},
    )
    assert resp.status_code == 404


class _OkClient:
    async def count(self) -> int:
        return 42

    async def version(self) -> str:
        return "26.02d"

    async def load(self, *args: Any, **kwargs: Any) -> None:
        pass


class _FailCountClient:
    async def count(self) -> int:
        raise StorageError("store unreachable")

    async def version(self) -> str:
        return "26.02d"


class _FailVersionClient:
    async def count(self) -> int:
        return 42

    async def version(self) -> str:
        raise StorageError("store unreachable")


class _OkCadsrRepo:
    def count(self) -> int:
        return 7

    def find_cdes_by_concept(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    def get_cde(self, *args: Any, **kwargs: Any) -> Any:
        return None

    def search(self, *args: Any, **kwargs: Any) -> Any:
        return None

    def list_cdes(self, *args: Any, **kwargs: Any) -> Any:
        return None

    def summaries_for(self, *args: Any, **kwargs: Any) -> Any:
        return None


class _FailCadsrRepo:
    def count(self) -> int:
        raise sqlite3.OperationalError("database locked")

    def find_cdes_by_concept(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    def get_cde(self, *args: Any, **kwargs: Any) -> Any:
        return None

    def search(self, *args: Any, **kwargs: Any) -> Any:
        return None

    def list_cdes(self, *args: Any, **kwargs: Any) -> Any:
        return None

    def summaries_for(self, *args: Any, **kwargs: Any) -> Any:
        return None


@pytest.mark.api
def test_refresh_with_failing_ncit_client_reports_unhealthy() -> None:
    app = create_app()
    app.dependency_overrides[get_ncit_client] = _FailCountClient
    with TestClient(app) as client:
        resp = client.post("/api/v1/refresh")
    assert resp.status_code == 200
    repos = {r["name"]: r for r in resp.json()["repositories"]}
    assert repos["ncit"]["healthy"] is False
    assert "store unreachable" in repos["ncit"]["error"]


@pytest.mark.api
def test_refresh_with_version_failure_reports_unhealthy() -> None:
    app = create_app()
    app.dependency_overrides[get_ncit_client] = _FailVersionClient
    with TestClient(app) as client:
        resp = client.post("/api/v1/refresh")
    assert resp.status_code == 200
    repos = {r["name"]: r for r in resp.json()["repositories"]}
    assert repos["ncit"]["healthy"] is False


@pytest.mark.api
def test_refresh_with_failing_cadsr_repo_reports_unhealthy() -> None:
    app = create_app()
    app.dependency_overrides[get_ncit_client] = _OkClient
    app.dependency_overrides[get_cadsr_repo] = _FailCadsrRepo
    with TestClient(app) as client:
        resp = client.post("/api/v1/refresh")
    assert resp.status_code == 200
    repos = {r["name"]: r for r in resp.json()["repositories"]}
    assert repos["cadsr"]["healthy"] is False
    assert "database locked" in repos["cadsr"]["error"]


@pytest.mark.api
def test_reload_success_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RELOAD_ALLOWED_DIR", str(tmp_path))
    ttl = tmp_path / "graph.ttl"
    ttl.write_text("@prefix ex: <http://e/> . ex:a ex:b ex:c .")
    app = create_app()
    app.dependency_overrides[get_ncit_client] = _OkClient
    with TestClient(app) as client:
        body = {"source_path": str(ttl), "replace": True}
        resp = client.post("/api/v1/refresh/ncit/reload", json=body)
    assert resp.status_code == 200
    assert resp.json() == {"triples_before": 42, "triples_after": 42}


class _FakeNcitStore:
    def __init__(self) -> None:
        self._call_count = 0

    async def list_concepts(self, *args: Any, **kwargs: Any) -> Any:
        return None

    async def labels_for(self, *args: Any, **kwargs: Any) -> dict[str, str]:
        return {}

    async def search(self, *args: Any, **kwargs: Any) -> Any:
        return None

    async def get_concept_detail(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def search_records(
        self, *args: Any, **kwargs: Any
    ) -> list[dict[str, str | None]]:
        self._call_count += 1
        if self._call_count >= 2:
            return []
        return [{"code": "C1", "label": "Test"}]

    async def embedding_records(
        self, *args: Any, **kwargs: Any
    ) -> list[dict[str, str | None]]:
        return []


class _FakeSearchIndex:
    async def search(self, *args: Any, **kwargs: Any) -> Any:
        return None

    async def populate(self, records: list[dict[str, str | None]]) -> int:
        return len(records)

    async def count(self) -> int:
        return 0

    async def rebuild(
        self, batches: AsyncIterator[Sequence[dict[str, str | None]]]
    ) -> int:
        total = 0
        async for records in batches:
            total += len(records) if records else 0
        return total


@pytest.mark.api
def test_rebuild_search_index_success() -> None:

    app = create_app()
    store = _FakeNcitStore()
    index = _FakeSearchIndex()
    app.dependency_overrides[get_ncit_store] = lambda: store
    app.dependency_overrides[get_ncit_search_index] = lambda: index
    with TestClient(app) as client:
        resp = client.post("/api/v1/refresh/ncit/search-index")
    assert resp.status_code == 200
    assert resp.json() == {"concepts_indexed": 1}


class _FailingSearchIndex:
    async def rebuild(self, *args: object, **kwargs: object) -> int:
        raise StorageError("store unreachable")


@pytest.mark.api
def test_rebuild_search_index_store_error_returns_502() -> None:
    app = create_app()
    app.dependency_overrides[get_ncit_store] = _FakeNcitStore
    app.dependency_overrides[get_ncit_search_index] = _FailingSearchIndex
    with TestClient(app) as client:
        resp = client.post("/api/v1/refresh/ncit/search-index")
    assert resp.status_code == 502
    assert "search-index" in resp.json()["detail"]


@pytest.mark.integration
@pytest.mark.full_build
def test_refresh_reports_ncit_version_and_counts(live_api_client: TestClient) -> None:
    resp = live_api_client.post("/api/v1/refresh")
    assert resp.status_code == 200
    body = resp.json()
    repos = {r["name"]: r for r in body["repositories"]}
    assert repos["ncit"]["healthy"] is True
    assert repos["ncit"]["version"] == "26.02d"
    assert repos["ncit"]["item_count"] == 12836426
    assert "cadsr" in repos
