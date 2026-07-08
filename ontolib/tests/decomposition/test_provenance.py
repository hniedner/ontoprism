"""Unit tests for ProvenanceStore using mocked session factory."""

from __future__ import annotations

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
