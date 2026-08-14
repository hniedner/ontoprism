"""Refresh endpoint tests: report (live), and reload guards (no store mutation)."""

import asyncio
from collections.abc import AsyncIterator, Iterator, Sequence
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.dependencies import (
    get_ncit_search_index,
    get_ncit_store,
    get_repository_metadata,
    get_uberon_search_index,
    get_uberon_store,
)
from backend.main import create_app
from backend.repository_metadata import RepositoryUnhealthy, UberonClassCounts
from ontolib.core.exceptions import StorageError
from ontolib.terminologies.uberon.store import (
    UberonIndexObservation,
    UberonServingFingerprint,
)


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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
    def __init__(self) -> None:
        self.source: tuple[str, str] | None = None

    async def search(self, *args: Any, **kwargs: Any) -> Any:
        return None

    async def populate(self, records: list[dict[str, str | None]]) -> int:
        return len(records)

    async def count(self) -> int:
        return 0

    async def rebuild(
        self,
        batches: AsyncIterator[Sequence[dict[str, str | None]]],
        *,
        source_identity: str,
        source_hash: str,
        validate_source: Any = None,
        expected_row_count: int | None = None,
    ) -> int:
        self.source = (source_identity, source_hash)
        total = 0
        async for records in batches:
            total += len(records) if records else 0
        if validate_source is not None:
            await validate_source()
        del expected_row_count
        return total


class _FakeUberonStore:
    def __init__(self) -> None:
        self.calls = 0

    async def search_records(
        self, *, limit: int, offset: int
    ) -> list[dict[str, str | None]]:
        del limit, offset
        self.calls += 1
        if self.calls > 1:
            return []
        return [
            {
                "code": "UBERON:0002048",
                "source": "uberon",
                "label": "lung",
                "synonyms": "",
            }
        ]


@pytest.mark.api
def test_rebuild_search_index_success() -> None:

    app = create_app()
    store = _FakeNcitStore()
    index = _FakeSearchIndex()
    app.dependency_overrides[get_ncit_store] = lambda: store
    app.dependency_overrides[get_ncit_search_index] = lambda: index
    app.dependency_overrides[get_repository_metadata] = lambda: SimpleNamespace(
        ncit=_ready_ncit
    )
    with TestClient(app) as client:
        resp = client.post("/api/v1/refresh/ncit/search-index")
    assert resp.status_code == 200
    assert resp.json() == {"concepts_indexed": 1}
    assert index.source is not None
    assert index.source[0] == "f" * 64


async def _ready_ncit() -> SimpleNamespace:
    return SimpleNamespace(source_identity="f" * 64)


async def _ready_uberon(*, force: bool = False) -> SimpleNamespace:
    assert force is True
    return SimpleNamespace(
        source_identity="a" * 64,
        source_sha256="b" * 64,
        class_counts=UberonClassCounts(
            uberon=16_362,
            cl=1_484,
            uberon_searchable=16_071,
            cl_searchable=1_484,
        ),
        observation=UberonIndexObservation(
            version_iri="expected",
            triples=900_000,
            has_uberon_lung=True,
            has_cell_class=True,
            has_ncit_xref=True,
            serving=UberonServingFingerprint(
                rows=100,
                sha256="f" * 64,
                uberon_classes=16_362,
                cl_classes=1_484,
                uberon_searchable_classes=16_071,
                cl_searchable_classes=1_484,
            ),
        ),
    )


@pytest.mark.api
def test_rebuild_uberon_search_index_binds_certified_source() -> None:
    app = create_app()
    store = _FakeUberonStore()
    index = _FakeSearchIndex()
    app.dependency_overrides[get_uberon_store] = lambda: store
    app.dependency_overrides[get_uberon_search_index] = lambda: index
    app.dependency_overrides[get_repository_metadata] = lambda: SimpleNamespace(
        uberon=_ready_uberon
    )
    ready = asyncio.run(_ready_uberon(force=True))

    async def stable_observation(
        _url: str,
    ) -> tuple[UberonIndexObservation, UberonClassCounts]:
        return ready.observation, ready.class_counts

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "backend.api.v1.refresh.observe_uberon_repository", stable_observation
    )

    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/refresh/uberon/search-index")
    finally:
        monkeypatch.undo()

    assert response.status_code == 200
    assert response.json() == {"concepts_indexed": 1}
    assert index.source == ("a" * 64, "f" * 64)


class _FailingSearchIndex:
    async def rebuild(self, *args: object, **kwargs: object) -> int:
        raise StorageError("store unreachable")


@pytest.mark.api
def test_rebuild_search_index_store_error_returns_502() -> None:
    app = create_app()
    app.dependency_overrides[get_ncit_store] = _FakeNcitStore
    app.dependency_overrides[get_ncit_search_index] = _FailingSearchIndex
    app.dependency_overrides[get_repository_metadata] = lambda: SimpleNamespace(
        ncit=_ready_ncit
    )
    with TestClient(app) as client:
        resp = client.post("/api/v1/refresh/ncit/search-index")
    assert resp.status_code == 502
    assert "search-index" in resp.json()["detail"]


async def _unhealthy_ncit() -> RepositoryUnhealthy:
    return RepositoryUnhealthy(
        repository="ncit",
        reason="activation-incomplete",
        message="NCIt activation did not complete.",
    )


@pytest.mark.api
def test_rebuild_search_index_unhealthy_repository_returns_503() -> None:
    app = create_app()
    index = _FakeSearchIndex()
    app.dependency_overrides[get_ncit_store] = _FakeNcitStore
    app.dependency_overrides[get_ncit_search_index] = lambda: index
    app.dependency_overrides[get_repository_metadata] = lambda: SimpleNamespace(
        ncit=_unhealthy_ncit
    )
    with TestClient(app) as client:
        resp = client.post("/api/v1/refresh/ncit/search-index")
    assert resp.status_code == 503
    assert resp.json()["detail"]["reason"] == "activation-incomplete"
    # An unhealthy proxy must not trigger a rebuild against a stale/absent store.
    assert index.source is None


class _DriftingNcitStore(_FakeNcitStore):
    """Its embedding fingerprint differs between the pre- and post-rebuild reads."""

    def __init__(self) -> None:
        super().__init__()
        self._fingerprint_pass = 0
        self._page_served = False

    async def embedding_records(
        self, *args: Any, **kwargs: Any
    ) -> list[dict[str, str | None]]:
        del args
        if kwargs.get("after") is None:
            self._fingerprint_pass += 1
            self._page_served = False
        if self._page_served:
            return []
        self._page_served = True
        code = "C1" if self._fingerprint_pass == 1 else "C2"
        return [{"iri": f"http://example.test/{code}", "code": code}]


@pytest.mark.api
def test_rebuild_search_index_source_change_mid_rebuild_returns_502() -> None:
    app = create_app()
    store = _DriftingNcitStore()
    index = _FakeSearchIndex()
    app.dependency_overrides[get_ncit_store] = lambda: store
    app.dependency_overrides[get_ncit_search_index] = lambda: index
    app.dependency_overrides[get_repository_metadata] = lambda: SimpleNamespace(
        ncit=_ready_ncit
    )
    with TestClient(app) as client:
        resp = client.post("/api/v1/refresh/ncit/search-index")
    # The post-rebuild fingerprint differs from the pre-rebuild one, so the built
    # index would be inconsistent with the store; the concurrency gate must reject it.
    assert resp.status_code == 502
    assert "search-index" in resp.json()["detail"]


@pytest.mark.integration
@pytest.mark.full_build
@pytest.mark.full_store
def test_refresh_reports_ncit_version_and_counts(
    live_api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "icdo_entitlement_key", "licensed")
    resp = live_api_client.post(
        "/api/v1/refresh", headers={"X-ICDO-Entitlement": "licensed"}
    )
    assert resp.status_code == 200
    body = resp.json()
    repos = {r["repository"]: r for r in body["repositories"]}
    assert repos["ncit"]["state"] == "ready"
    assert repos["ncit"]["release"] == "26.07d"
    assert repos["ncit"]["observation"]["default_triples"] == 12_980_813
    assert repos["ncit"]["observation"]["stated_triples"] == 10_855_010
    assert repos["cadsr"]["state"] == "ready"
    assert (
        repos["cadsr"]["source_identity"] == repos["cadsr"]["source"]["archive_sha256"]
    )
