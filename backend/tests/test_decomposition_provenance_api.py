"""Hermetic API tests for decomposition provenance endpoints (fake store)."""

from collections.abc import Iterator
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from backend.dependencies import get_provenance_store
from backend.main import create_app
from ontolib.decomposition.provenance_models import MintedConcept, RunSummary


class _FakeProvenanceStore:
    def __init__(
        self,
        runs: list[RunSummary] | None = None,
        mints: list[MintedConcept] | None = None,
    ) -> None:
        self._runs = runs or []
        self._mints = mints or []

    async def list_runs(self, limit: int = 50, offset: int = 0) -> list[RunSummary]:
        return self._runs[offset : offset + limit]

    async def get_run(self, run_id: str) -> RunSummary | None:
        for r in self._runs:
            if r.id == run_id:
                return r
        return None

    async def list_minted_concepts(
        self,
        run_id: str | None = None,
        status: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[MintedConcept]:
        filtered = self._mints
        if run_id is not None:
            filtered = [m for m in filtered if m.run_id == run_id]
        if status is not None:
            filtered = [m for m in filtered if m.status == status]
        return filtered[offset : offset + limit]


class _ErrorFakeStore:
    """Always raises SQLAlchemyError to test the 503 path."""

    async def list_runs(self, limit: int = 50, offset: int = 0) -> list[RunSummary]:
        msg = "fake db error"
        raise SQLAlchemyError(msg)

    async def get_run(self, run_id: str) -> RunSummary | None:
        msg = "fake db error"
        raise SQLAlchemyError(msg)

    async def list_minted_concepts(
        self,
        run_id: str | None = None,
        status: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[MintedConcept]:
        msg = "fake db error"
        raise SQLAlchemyError(msg)


_SAMPLE_RUN = RunSummary(
    id="run-1",
    branch="neoplasm",
    status="complete",
    ncit_version="26.05d",
    started_at=datetime(2026, 7, 12, 0, 0, tzinfo=timezone.utc),  # noqa: UP017
    finished_at=datetime(2026, 7, 12, 1, 0, tzinfo=timezone.utc),  # noqa: UP017
    total_in_scope=5,
    decomposed=3,
    residual=2,
    minted_count=1,
    pct_decomposed=0.6,
    roundtrip_fidelity=0.95,
)

_SAMPLE_INCOMPLETE_RUN = RunSummary(
    id="run-2",
    branch="neoplasm",
    status="running",
    ncit_version="26.05d",
    started_at=datetime(2026, 7, 12, 0, 0, tzinfo=timezone.utc),  # noqa: UP017
)

_SAMPLE_MINT = MintedConcept(
    id="MINT-abc",
    run_id="run-1",
    axis="op:Laterality",
    label="Left",
    source_signal="Left Atrial Myxoma",
    status="proposed",
)


def _client(
    fake: _FakeProvenanceStore | _ErrorFakeStore,
) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_provenance_store] = lambda: fake
    with TestClient(app) as client:
        yield client


@pytest.mark.api
def test_list_runs_returns_summaries() -> None:
    fake = _FakeProvenanceStore(runs=[_SAMPLE_RUN, _SAMPLE_INCOMPLETE_RUN])
    client = next(_client(fake))
    resp = client.get("/api/v1/decomposition/runs")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    complete = next(r for r in body if r["status"] == "complete")
    assert complete["total_in_scope"] == 5
    assert complete["decomposed"] == 3
    assert complete["residual"] == 2
    assert complete["minted_count"] == 1
    assert complete["pct_decomposed"] == 0.6
    assert complete["roundtrip_fidelity"] == 0.95
    running = next(r for r in body if r["status"] == "running")
    assert running["total_in_scope"] is None
    assert running["finished_at"] is None


@pytest.mark.api
def test_list_runs_with_limit_and_offset() -> None:
    fake = _FakeProvenanceStore(runs=[_SAMPLE_RUN, _SAMPLE_INCOMPLETE_RUN])
    client = next(_client(fake))
    resp = client.get("/api/v1/decomposition/runs?limit=1&offset=1")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == "run-2"


@pytest.mark.api
def test_get_run_found() -> None:
    fake = _FakeProvenanceStore(runs=[_SAMPLE_RUN])
    client = next(_client(fake))
    resp = client.get("/api/v1/decomposition/runs/run-1")
    assert resp.status_code == 200
    assert resp.json()["id"] == "run-1"


@pytest.mark.api
def test_get_run_not_found_404() -> None:
    fake = _FakeProvenanceStore(runs=[_SAMPLE_RUN])
    client = next(_client(fake))
    resp = client.get("/api/v1/decomposition/runs/nonexistent")
    assert resp.status_code == 404


@pytest.mark.api
def test_list_minted_concepts_returns_all() -> None:
    fake = _FakeProvenanceStore(mints=[_SAMPLE_MINT])
    client = next(_client(fake))
    resp = client.get("/api/v1/decomposition/minted-concepts")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == "MINT-abc"
    assert body[0]["source_signal"] == "Left Atrial Myxoma"


@pytest.mark.api
def test_list_minted_concepts_filtered_by_run_id() -> None:
    other = MintedConcept(
        id="MINT-xyz",
        run_id="run-2",
        axis="op:Morphology",
        label="Adenoma",
        source_signal="Adenoma",
        status="approved",
    )
    fake = _FakeProvenanceStore(mints=[_SAMPLE_MINT, other])
    client = next(_client(fake))
    resp = client.get("/api/v1/decomposition/minted-concepts?run_id=run-2")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == "MINT-xyz"


@pytest.mark.api
def test_list_minted_concepts_filtered_by_status() -> None:
    other = MintedConcept(
        id="MINT-xyz",
        run_id="run-1",
        axis="op:Morphology",
        label="Adenoma",
        source_signal="Adenoma",
        status="approved",
    )
    fake = _FakeProvenanceStore(mints=[_SAMPLE_MINT, other])
    client = next(_client(fake))
    resp = client.get("/api/v1/decomposition/minted-concepts?status=approved")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == "MINT-xyz"


@pytest.mark.api
def test_list_minted_concepts_empty_when_no_match() -> None:
    fake = _FakeProvenanceStore(mints=[_SAMPLE_MINT])
    client = next(_client(fake))
    resp = client.get("/api/v1/decomposition/minted-concepts?run_id=nonexistent")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.api
def test_list_minted_concepts_with_limit_and_offset() -> None:
    mints = [
        MintedConcept(
            id=f"MINT-{i}",
            run_id="run-1",
            axis="op:A",
            label="A",
            source_signal="S",
            status="proposed",
        )
        for i in range(3)
    ]
    fake = _FakeProvenanceStore(mints=mints)
    client = next(_client(fake))
    resp = client.get("/api/v1/decomposition/minted-concepts?limit=2&offset=1")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert body[0]["id"] == "MINT-1"
    assert body[1]["id"] == "MINT-2"


@pytest.mark.api
def test_list_runs_503_on_db_error() -> None:
    client = next(_client(_ErrorFakeStore()))
    resp = client.get("/api/v1/decomposition/runs")
    assert resp.status_code == 503


@pytest.mark.api
def test_get_run_503_on_db_error() -> None:
    client = next(_client(_ErrorFakeStore()))
    resp = client.get("/api/v1/decomposition/runs/run-1")
    assert resp.status_code == 503


@pytest.mark.api
def test_list_minted_concepts_503_on_db_error() -> None:
    client = next(_client(_ErrorFakeStore()))
    resp = client.get("/api/v1/decomposition/minted-concepts")
    assert resp.status_code == 503
