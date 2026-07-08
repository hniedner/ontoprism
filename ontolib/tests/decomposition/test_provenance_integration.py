"""Integration tests for ProvenanceStore against a real Postgres (design §4.5).

Every existing unit test in ``test_provenance.py`` mocks the session entirely — none
of the raw SQL (composite-key ``ON CONFLICT``, the FK to ``decomp_run``, the ``jsonb``
metrics column) has ever run against a real database. This round-trips the store
against the project's live Postgres and cleans up unconditionally. Skips when
Postgres is unreachable.
"""

from __future__ import annotations

import asyncpg
import pytest

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.decomposition.models import Constituent
from ontolib.decomposition.provenance import ProvenanceStore

_RUN_ID = "test-provenance-integration-run"


def _asyncpg_dsn(sqlalchemy_url: str) -> str:
    return sqlalchemy_url.replace("+asyncpg", "")


async def _pg_reachable(dsn: str) -> bool:
    try:
        conn = await asyncpg.connect(dsn, timeout=2)
    except (OSError, asyncpg.PostgresError):
        return False
    await conn.close()
    return True


async def _cleanup(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("DELETE FROM decomp_constituent WHERE run_id = $1", _RUN_ID)
        await conn.execute("DELETE FROM minted_concept WHERE run_id = $1", _RUN_ID)
        await conn.execute("DELETE FROM decomp_run WHERE id = $1", _RUN_ID)
    finally:
        await conn.close()


@pytest.mark.integration
async def test_run_manifest_round_trips_against_real_postgres() -> None:
    dsn = _asyncpg_dsn(get_settings().database_url)
    if not await _pg_reachable(dsn):
        pytest.skip("Postgres not reachable")

    engine = make_engine(get_settings().database_url)
    sf = make_sessionmaker(engine)
    store = ProvenanceStore(sf)
    try:
        await _cleanup(dsn)  # in case a prior run left rows behind

        await store.upsert_run(_RUN_ID, "neoplasm", "26.02d")
        await store.upsert_constituents(
            _RUN_ID,
            "C6135",
            [Constituent(axis="R88", filler_code="C27970", axis_source="role")],
        )
        processed = await store.processed_codes(_RUN_ID)
        assert processed == {"C6135"}

        finished = await store.finish_run(
            _RUN_ID, metrics={"decomposed": 1, "total_in_scope": 1}
        )
        assert finished is True
    finally:
        await _cleanup(dsn)
        await dispose_engine(engine)


@pytest.mark.integration
async def test_minted_concept_status_survives_a_rerun() -> None:
    # The regression this test pins: a rerun re-mints the same deterministic id with
    # status="proposed" by default (minting.py); the engine's upsert must never
    # clobber a curator's prior approve/reject decision on that row.
    dsn = _asyncpg_dsn(get_settings().database_url)
    if not await _pg_reachable(dsn):
        pytest.skip("Postgres not reachable")

    engine = make_engine(get_settings().database_url)
    sf = make_sessionmaker(engine)
    store = ProvenanceStore(sf)
    mint_id = "MINT-test-provenance"
    try:
        await _cleanup(dsn)
        await store.upsert_run(_RUN_ID, "neoplasm", "26.02d")

        await store.upsert_minted_concept(
            _RUN_ID, id=mint_id, axis="op:Laterality", label="Left"
        )

        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(
                "UPDATE minted_concept SET status = 'approved' WHERE id = $1",
                mint_id,
            )
        finally:
            await conn.close()

        # A "rerun" re-mints the identical proposal — same id, default status.
        await store.upsert_minted_concept(
            _RUN_ID, id=mint_id, axis="op:Laterality", label="Left", status="proposed"
        )

        conn = await asyncpg.connect(dsn)
        try:
            status = await conn.fetchval(
                "SELECT status FROM minted_concept WHERE id = $1", mint_id
            )
        finally:
            await conn.close()
        assert status == "approved"  # the curator's decision was NOT clobbered
    finally:
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("DELETE FROM minted_concept WHERE id = $1", mint_id)
        finally:
            await conn.close()
        await _cleanup(dsn)
        await dispose_engine(engine)
