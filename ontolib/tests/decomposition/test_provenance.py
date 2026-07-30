"""Unit tests for ProvenanceStore using mocked session factory."""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from ontolib.decomposition.models import (
    CompleteDefinition,
    Constituent,
    Decomposition,
    GenusDefinitionFact,
    RestrictionDefinitionFact,
)
from ontolib.decomposition.provenance import ProvenanceStore
from ontolib.decomposition.provenance_models import RunFingerprint


def _make_mock_sf(*, rowcount: int = 1) -> MagicMock:
    """Create a mock async session factory."""
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    transaction = AsyncMock()
    transaction.__aenter__ = AsyncMock(return_value=None)
    transaction.__aexit__ = AsyncMock(return_value=False)
    mock_session.begin = MagicMock(return_value=transaction)
    result_mock = MagicMock(rowcount=rowcount)
    mock_session.execute.return_value = result_mock
    return MagicMock(return_value=mock_session)


@pytest.mark.unit
async def test_finish_run_sets_complete() -> None:
    sf = _make_mock_sf(rowcount=1)
    locked = MagicMock()
    fingerprint = RunFingerprint(
        source_identity="a" * 64,
        branch="neoplasm",
        semantic_types=("Neoplastic Process",),
        worklist=(),
        algorithm_version="decomposition-v1",
        config_version="axes-v1",
        walker_max_depth=5,
        output_mode="none",
        load_mode="none",
        emitted_at=datetime.datetime(2026, 7, 29, tzinfo=datetime.UTC),
    )
    locked.mappings.return_value.first.return_value = {
        "status": "running",
        "source_identity": "a" * 64,
        "fingerprint": fingerprint.model_dump(mode="json"),
        "fingerprint_sha256": fingerprint.identity,
    }
    worklist = MagicMock()
    worklist.scalars.return_value.all.return_value = []
    incomplete = MagicMock()
    incomplete.scalar_one.return_value = 0
    promoted = MagicMock()
    updated = MagicMock(rowcount=1)
    sf().execute.side_effect = [locked, worklist, incomplete, promoted, updated]
    store = ProvenanceStore(sf)
    result = await store.finish_run(
        "run-1",
        source_identity="a" * 64,
        metrics={"total_in_scope": 0},
    )
    assert result is True


@pytest.mark.unit
async def test_finish_run_noop_returns_false() -> None:
    sf = _make_mock_sf()
    missing = MagicMock()
    missing.mappings.return_value.first.return_value = None
    sf().execute.side_effect = [missing]
    store = ProvenanceStore(sf)
    updated = await store.finish_run(
        "nonexistent",
        source_identity="a" * 64,
        metrics={},
    )
    assert updated is False


@pytest.mark.unit
async def test_list_runs_returns_summaries_with_parsed_metrics() -> None:
    sf = _make_mock_sf()
    result_mock = sf().execute.return_value
    result_mock.mappings.return_value.all.return_value = [
        {
            "id": "run-1",
            "branch": "neoplasm",
            "status": "complete",
            "ncit_version": "26.05d",
            "started_at": datetime.datetime(2026, 7, 12, 0, 0, tzinfo=datetime.UTC),
            "finished_at": datetime.datetime(2026, 7, 12, 1, 0, tzinfo=datetime.UTC),
            "metrics": '{"total_in_scope":5,"decomposed":3,"residual":2,'
            '"minted_count":1,"pct_decomposed":0.6,"roundtrip_fidelity":0.95}',
        },
    ]
    store = ProvenanceStore(sf)
    runs = await store.list_runs()
    assert len(runs) == 1
    r = runs[0]
    assert r.id == "run-1"
    assert r.branch == "neoplasm"
    assert r.status == "complete"
    assert r.ncit_version == "26.05d"
    assert r.total_in_scope == 5
    assert r.decomposed == 3
    assert r.residual == 2
    assert r.minted_count == 1
    assert r.pct_decomposed == 0.6
    assert r.roundtrip_fidelity == 0.95
    assert r.finished_at is not None


@pytest.mark.unit
async def test_list_runs_metrics_none_when_null() -> None:
    sf = _make_mock_sf()
    result_mock = sf().execute.return_value
    result_mock.mappings.return_value.all.return_value = [
        {
            "id": "run-2",
            "branch": "neoplasm",
            "status": "running",
            "ncit_version": "26.05d",
            "started_at": datetime.datetime(2026, 7, 12, 0, 0, tzinfo=datetime.UTC),
            "finished_at": None,
            "metrics": None,
        },
    ]
    store = ProvenanceStore(sf)
    runs = await store.list_runs()
    assert len(runs) == 1
    r = runs[0]
    assert r.status == "running"
    assert r.total_in_scope is None
    assert r.decomposed is None
    assert r.residual is None
    assert r.minted_count is None
    assert r.pct_decomposed is None
    assert r.roundtrip_fidelity is None
    assert r.finished_at is None


@pytest.mark.unit
async def test_list_runs_corrupt_metrics_falls_back_to_empty() -> None:
    sf = _make_mock_sf()
    result_mock = sf().execute.return_value
    result_mock.mappings.return_value.all.return_value = [
        {
            "id": "run-1",
            "branch": "neoplasm",
            "status": "complete",
            "ncit_version": "26.05d",
            "started_at": datetime.datetime(2026, 7, 12, 0, 0, tzinfo=datetime.UTC),
            "finished_at": None,
            "metrics": "not valid json",
        },
    ]
    store = ProvenanceStore(sf)
    runs = await store.list_runs()
    assert len(runs) == 1
    r = runs[0]
    assert r.total_in_scope is None  # corrupt → fallback to {}
    assert r.decomposed is None


@pytest.mark.unit
async def test_list_runs_empty_when_no_rows() -> None:
    sf = _make_mock_sf()
    result_mock = sf().execute.return_value
    result_mock.mappings.return_value.all.return_value = []
    store = ProvenanceStore(sf)
    runs = await store.list_runs()
    assert runs == []


@pytest.mark.unit
async def test_get_run_found() -> None:
    sf = _make_mock_sf()
    result_mock = sf().execute.return_value
    result_mock.mappings.return_value.first.return_value = {
        "id": "run-1",
        "branch": "neoplasm",
        "status": "complete",
        "ncit_version": "26.05d",
        "started_at": datetime.datetime(2026, 7, 12, 0, 0, tzinfo=datetime.UTC),
        "finished_at": None,
        "metrics": None,
    }
    store = ProvenanceStore(sf)
    run = await store.get_run("run-1")
    assert run is not None
    assert run.id == "run-1"


@pytest.mark.unit
async def test_get_run_not_found() -> None:
    sf = _make_mock_sf()
    result_mock = sf().execute.return_value
    result_mock.mappings.return_value.first.return_value = None
    store = ProvenanceStore(sf)
    run = await store.get_run("nonexistent")
    assert run is None


@pytest.mark.unit
async def test_list_minted_concepts_returns_all() -> None:
    sf = _make_mock_sf()
    result_mock = sf().execute.return_value
    result_mock.mappings.return_value.all.return_value = [
        {
            "id": "MINT-abc",
            "run_id": "run-1",
            "axis": "op:Laterality",
            "label": "Left",
            "source_signal": "Left Atrial Myxoma",
            "status": "proposed",
        },
    ]
    store = ProvenanceStore(sf)
    mints = await store.list_minted_concepts()
    assert len(mints) == 1
    m = mints[0]
    assert m.id == "MINT-abc"
    assert m.run_id == "run-1"
    assert m.axis == "op:Laterality"
    assert m.label == "Left"
    assert m.source_signal == "Left Atrial Myxoma"
    assert m.status == "proposed"


@pytest.mark.unit
async def test_list_minted_concepts_filtered_by_run_id_and_status() -> None:
    sf = _make_mock_sf()
    result_mock = sf().execute.return_value
    result_mock.mappings.return_value.all.return_value = []
    store = ProvenanceStore(sf)
    mints = await store.list_minted_concepts(run_id="run-1", status="approved")
    assert mints == []
    # Verify both filters were passed as the second positional argument to execute.
    args, _ = sf().execute.call_args
    params = args[1]
    assert params["run_id"] == "run-1"
    assert params["status"] == "approved"


@pytest.mark.unit
async def test_list_minted_concepts_empty_when_no_rows() -> None:
    sf = _make_mock_sf()
    result_mock = sf().execute.return_value
    result_mock.mappings.return_value.all.return_value = []
    store = ProvenanceStore(sf)
    mints = await store.list_minted_concepts()
    assert mints == []


@pytest.mark.unit
async def test_list_minted_concepts_limit_offset() -> None:
    sf = _make_mock_sf()
    result_mock = sf().execute.return_value
    result_mock.mappings.return_value.all.return_value = [
        {
            "id": "MINT-1",
            "run_id": "run-1",
            "axis": "op:A",
            "label": "A",
            "source_signal": "SigA",
            "status": "proposed",
        },
        {
            "id": "MINT-2",
            "run_id": "run-1",
            "axis": "op:B",
            "label": "B",
            "source_signal": "SigB",
            "status": "proposed",
        },
    ]
    store = ProvenanceStore(sf)
    mints = await store.list_minted_concepts(limit=1, offset=1)
    # DB does the filtering; mock returns all rows, so we verify
    # that limit/offset were passed as query parameters.
    assert len(mints) == 2
    args, _ = sf().execute.call_args
    params = args[1]
    assert params["limit"] == 1
    assert params["offset"] == 1


@pytest.mark.unit
async def test_decompositions_for_run_reconstructs_complete_typed_record() -> None:
    sf = _make_mock_sf()
    restriction_id = "b" * 64
    work_items = MagicMock()
    work_items.mappings.return_value.all.return_value = [
        {"concept_code": "C1", "semantic_type": "Neoplastic Process"}
    ]
    constituents = MagicMock()
    constituents.mappings.return_value.all.return_value = [
        {
            "concept_code": "C1",
            "axis": "R101",
            "filler_code": "C200",
            "axis_source": "role",
            "most_specific": True,
            "needs_review": True,
            "relationship_group": "anatomy-1",
            "source_definition_ids": f'["{restriction_id}"]',
        }
    ]
    definitions = MagicMock()
    definitions.mappings.return_value.all.return_value = [
        {
            "concept_code": "C1",
            "fact_id": "a" * 64,
            "anchor_code": "C1",
            "group_id": "c" * 64,
            "depth": 0,
            "fact_kind": "genus",
            "genus_code": "C100",
            "is_defined": True,
            "role_code": None,
            "filler_code": None,
        },
        {
            "concept_code": "C1",
            "fact_id": "b" * 64,
            "anchor_code": "C1",
            "group_id": "c" * 64,
            "depth": 0,
            "fact_kind": "restriction",
            "genus_code": None,
            "is_defined": None,
            "role_code": "R101",
            "filler_code": "C200",
        },
    ]
    sf().execute.side_effect = [work_items, constituents, definitions]
    store = ProvenanceStore(sf)

    assert await store.decompositions_for_run("run-1") == [
        Decomposition(
            code="C1",
            semantic_type="Neoplastic Process",
            constituents=[
                Constituent(
                    axis="R101",
                    filler_code="C200",
                    axis_source="role",
                    most_specific=True,
                    needs_review=True,
                    group="anatomy-1",
                    source_definition_ids=(restriction_id,),
                )
            ],
            complete_definition=CompleteDefinition(
                root_code="C1",
                facts=(
                    GenusDefinitionFact(
                        fact_id="a" * 64,
                        anchor_code="C1",
                        group_id="c" * 64,
                        depth=0,
                        genus_code="C100",
                        is_defined=True,
                    ),
                    RestrictionDefinitionFact(
                        fact_id="b" * 64,
                        anchor_code="C1",
                        group_id="c" * 64,
                        depth=0,
                        role_code="R101",
                        filler_code="C200",
                    ),
                ),
            ),
        )
    ]
