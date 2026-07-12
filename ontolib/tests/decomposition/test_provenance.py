"""Unit tests for ProvenanceStore using mocked session factory."""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from ontolib.decomposition.models import Constituent
from ontolib.decomposition.provenance import ProvenanceStore


def _make_mock_sf(*, rowcount: int = 1) -> MagicMock:
    """Create a mock async session factory."""
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    result_mock = MagicMock(rowcount=rowcount)
    mock_session.execute.return_value = result_mock
    return MagicMock(return_value=mock_session)


@pytest.mark.unit
async def test_upsert_run_inserts_correctly() -> None:
    sf = _make_mock_sf()
    store = ProvenanceStore(sf)
    result = await store.upsert_run("run-1", "neoplasm", "26.05d")
    assert result == 1
    assert sf().execute.call_count == 1
    assert sf().commit.call_count == 1


@pytest.mark.unit
async def test_upsert_constituents_batch_insert() -> None:
    sf = _make_mock_sf(rowcount=2)
    store = ProvenanceStore(sf)
    constituents = [
        Constituent(axis="R88", filler_code="C27970", axis_source="role"),
        Constituent(axis="R101", filler_code="C12400", axis_source="role"),
    ]
    count = await store.upsert_constituents("run-1", "C6135", constituents)
    assert count == 2
    sf().execute.assert_called_once()


@pytest.mark.unit
async def test_upsert_constituents_empty_is_noop() -> None:
    sf = _make_mock_sf()
    store = ProvenanceStore(sf)
    count = await store.upsert_constituents("run-1", "C6135", [])
    assert count == 0
    sf().execute.assert_not_called()


@pytest.mark.unit
async def test_finish_run_sets_complete() -> None:
    sf = _make_mock_sf(rowcount=1)
    store = ProvenanceStore(sf)
    updated = await store.finish_run("run-1")
    assert updated is True


@pytest.mark.unit
async def test_finish_run_noop_returns_false() -> None:
    sf = _make_mock_sf(rowcount=0)
    store = ProvenanceStore(sf)
    updated = await store.finish_run("nonexistent")
    assert updated is False


@pytest.mark.unit
async def test_processed_codes_returns_distinct_concept_codes() -> None:
    sf = _make_mock_sf()
    result_mock = sf().execute.return_value
    result_mock.scalars.return_value.all.return_value = ["C6135", "C4791"]
    store = ProvenanceStore(sf)
    codes = await store.processed_codes("run-1")
    assert codes == {"C6135", "C4791"}
    sf().execute.assert_called_once()


@pytest.mark.unit
async def test_processed_codes_empty_when_no_rows() -> None:
    sf = _make_mock_sf()
    result_mock = sf().execute.return_value
    result_mock.scalars.return_value.all.return_value = []
    store = ProvenanceStore(sf)
    codes = await store.processed_codes("run-1")
    assert codes == set()


@pytest.mark.unit
async def test_run_version_returns_stored_version() -> None:
    sf = _make_mock_sf()
    result_mock = sf().execute.return_value
    result_mock.scalar.return_value = "26.02d"
    store = ProvenanceStore(sf)
    version = await store.run_version("run-1")
    assert version == "26.02d"


@pytest.mark.unit
async def test_run_version_none_when_run_not_found() -> None:
    sf = _make_mock_sf()
    result_mock = sf().execute.return_value
    result_mock.scalar.return_value = None
    store = ProvenanceStore(sf)
    version = await store.run_version("nonexistent")
    assert version is None


@pytest.mark.unit
async def test_upsert_minted_concept() -> None:
    sf = _make_mock_sf()
    store = ProvenanceStore(sf)
    count = await store.upsert_minted_concept(
        run_id="run-1",
        id="MINT-abc123",
        axis="op:Laterality",
        label="Left",
        source_signal="Left Atrial Myxoma",
    )
    assert count == 1
    sf().execute.assert_called_once()


@pytest.mark.unit
async def test_upsert_minted_concept_never_overwrites_an_existing_row() -> None:
    # A rerun re-mints the same deterministic id (minting.py) with status="proposed"
    # by default. The engine must never clobber a curator's prior approve/reject
    # decision — so the upsert is insert-or-ignore, not insert-or-update, on conflict.
    sf = _make_mock_sf()
    store = ProvenanceStore(sf)
    await store.upsert_minted_concept(
        run_id="run-1", id="MINT-abc123", axis="op:Laterality", label="Left"
    )
    executed_sql = str(sf().execute.call_args.args[0])
    assert "DO NOTHING" in executed_sql
    assert "DO UPDATE" not in executed_sql


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
