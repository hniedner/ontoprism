"""Hermetic API tests for decomposition provenance endpoints (fake store)."""

from collections.abc import Iterator
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from backend.dependencies import get_provenance_store
from backend.main import create_app
from ontolib.decomposition.provenance_models import (
    MintedConcept,
    RunSummary,
    WorkItemOutcome,
)


class _FakeProvenanceStore:
    def __init__(
        self,
        runs: list[RunSummary] | None = None,
        mints: list[MintedConcept] | None = None,
        outcomes: list[WorkItemOutcome] | None = None,
    ) -> None:
        self._runs = runs or []
        self._mints = mints or []
        self._outcomes = outcomes or []

    async def list_runs(self, limit: int = 50, offset: int = 0) -> list[RunSummary]:
        return self._runs[offset : offset + limit]

    async def get_run(self, run_id: str) -> RunSummary | None:
        for r in self._runs:
            if r.id == run_id:
                return r
        return None

    async def work_item_outcomes(self, run_id: str) -> list[WorkItemOutcome]:
        return [outcome for outcome in self._outcomes if outcome.run_id == run_id]

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

    async def work_item_outcomes(self, run_id: str) -> list[WorkItemOutcome]:
        del run_id
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
    source_identity="a" * 64,
    fingerprint_sha256="b" * 64,
    started_at=datetime(2026, 7, 12, 0, 0, tzinfo=timezone.utc),  # noqa: UP017
    finished_at=datetime(2026, 7, 12, 1, 0, tzinfo=timezone.utc),  # noqa: UP017
    publication_state="published",
    publication_attempt_count=2,
    representation_identity="c" * 64,
    publication_artifact_path="/data/ncit_decomposed.ttl",
    publication_built_at=datetime(2026, 7, 12, 0, 55, tzinfo=timezone.utc),  # noqa: UP017
    publication_started_at=datetime(2026, 7, 12, 0, 56, tzinfo=timezone.utc),  # noqa: UP017
    publication_finished_at=datetime(2026, 7, 12, 1, 0, tzinfo=timezone.utc),  # noqa: UP017
    total_in_scope=5,
    decomposed=3,
    residual=2,
    semantic_excluded=1,
    atomic_noop=1,
    unknown_outcome=0,
    residual_precoordinated_count=1,
    residual_precoordination=1 / 3,
    minted_count=1,
    complete_definition_count=3,
    complete_fact_count=12,
    projected_fact_count=9,
    projection_loss_count=3,
    projection_loss_rate=0.25,
    pct_decomposed=0.6,
    roundtrip_fidelity=0.95,
)

_SAMPLE_INCOMPLETE_RUN = RunSummary(
    id="run-2",
    branch="neoplasm",
    status="running",
    ncit_version="26.05d",
    started_at=datetime(2026, 7, 12, 0, 0, tzinfo=timezone.utc),  # noqa: UP017
    publication_state="pending",
)

_SAMPLE_MINT = MintedConcept(
    id="MINT-abc",
    run_id="run-1",
    axis="op:Laterality",
    label="Left",
    source_signal="Left Atrial Myxoma",
    status="proposed",
)

_SAMPLE_OUTCOME = WorkItemOutcome(
    run_id="run-1",
    concept_code="C162770",
    ordinal=4,
    state="complete",
    outcome="semantic-excluded",
    semantic_type="Finding",
    semantic_types=("Finding",),
    is_decomposed=False,
    is_residual=False,
    constituent_count=0,
    minted_count=0,
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
    assert complete["semantic_excluded"] == 1
    assert complete["atomic_noop"] == 1
    assert complete["unknown_outcome"] == 0
    assert complete["residual_precoordinated_count"] == 1
    assert complete["residual_precoordination"] == pytest.approx(1 / 3)
    assert complete["minted_count"] == 1
    assert complete["complete_definition_count"] == 3
    assert complete["complete_fact_count"] == 12
    assert complete["projected_fact_count"] == 9
    assert complete["projection_loss_count"] == 3
    assert complete["projection_loss_rate"] == 0.25
    assert complete["pct_decomposed"] == 0.6
    assert complete["roundtrip_fidelity"] == 0.95
    assert complete["source_identity"] == "a" * 64
    assert complete["fingerprint_sha256"] == "b" * 64
    assert complete["error_type"] is None
    assert complete["publication_state"] == "published"
    assert complete["publication_attempt_count"] == 2
    assert complete["representation_identity"] == "c" * 64
    assert complete["publication_built_at"] == "2026-07-12T00:55:00Z"
    assert complete["publication_finished_at"] == "2026-07-12T01:00:00Z"
    assert complete["publication_error_type"] is None
    running = next(r for r in body if r["status"] == "running")
    assert running["publication_state"] == "pending"
    assert running["total_in_scope"] is None
    assert running["residual_precoordinated_count"] is None
    assert running["projection_loss_rate"] is None
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
def test_list_run_outcomes_explains_each_completed_work_item() -> None:
    fake = _FakeProvenanceStore(outcomes=[_SAMPLE_OUTCOME])
    client = next(_client(fake))

    resp = client.get("/api/v1/decomposition/runs/run-1/outcomes")

    assert resp.status_code == 200
    assert resp.json() == [
        {
            "run_id": "run-1",
            "concept_code": "C162770",
            "ordinal": 4,
            "state": "complete",
            "outcome": "semantic-excluded",
            "semantic_type": "Finding",
            "semantic_types": ["Finding"],
            "is_decomposed": False,
            "is_residual": False,
            "constituent_count": 0,
            "minted_count": 0,
        }
    ]


@pytest.mark.api
def test_axis_contract_catalogue_is_served_without_database_access() -> None:
    client = next(_client(_ErrorFakeStore()))

    resp = client.get("/api/v1/decomposition/axes")

    assert resp.status_code == 200
    primary = next(item for item in resp.json() if item["axis"] == "op:PrimarySite")
    assert primary["source_roles"] == ["R101"]
    assert primary["domain_code"] == "C7057"
    assert primary["range_code"] == "C12219"
    assert primary["definition"]
    assert primary["provenance"]


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
def test_list_run_outcomes_503_on_db_error() -> None:
    client = next(_client(_ErrorFakeStore()))
    resp = client.get("/api/v1/decomposition/runs/run-1/outcomes")
    assert resp.status_code == 503


@pytest.mark.api
def test_list_minted_concepts_503_on_db_error() -> None:
    client = next(_client(_ErrorFakeStore()))
    resp = client.get("/api/v1/decomposition/minted-concepts")
    assert resp.status_code == 503
