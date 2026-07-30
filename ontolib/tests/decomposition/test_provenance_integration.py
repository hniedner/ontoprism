"""Integration tests for ProvenanceStore against a real Postgres (design §4.5).

Every existing unit test in ``test_provenance.py`` mocks the session entirely — none
of the raw SQL (composite-key ``ON CONFLICT``, the FK to ``decomp_run``, the ``jsonb``
metrics column) has ever run against a real database. This round-trips the store
against a run-owned disposable Postgres database and cleans up exactly. Fails when
Postgres is unreachable.
"""

from __future__ import annotations

import datetime

import asyncpg
import pytest

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.decomposition.minting import MintedConcept
from ontolib.decomposition.models import Constituent, Decomposition
from ontolib.decomposition.provenance import ProvenanceStore
from ontolib.decomposition.provenance_models import RunFingerprint

_RUN_ID = "test-provenance-integration-run"
_RERUN_ID = "test-provenance-integration-rerun"

pytestmark = [
    pytest.mark.mutating_integration,
    pytest.mark.usefixtures("isolated_postgres_settings"),
]


def _asyncpg_dsn(sqlalchemy_url: str) -> str:
    return sqlalchemy_url.replace("+asyncpg", "")


def _fingerprint(worklist: tuple[str, ...]) -> RunFingerprint:
    return RunFingerprint(
        source_identity="a" * 64,
        branch="neoplasm",
        semantic_types=("Neoplastic Process",),
        worklist=worklist,
        total_limit=None,
        algorithm_version="decomposition-v1",
        config_version="axes-v1",
        walker_max_depth=5,
        output_mode="none",
        load_mode="none",
        emitted_at=datetime.datetime(2026, 7, 29, tzinfo=datetime.UTC),
    )


async def _cleanup(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        run_ids = [_RUN_ID, _RERUN_ID]
        await conn.execute(
            "DELETE FROM decomp_constituent WHERE run_id = ANY($1)", run_ids
        )
        await conn.execute("DELETE FROM minted_concept WHERE run_id = ANY($1)", run_ids)
        await conn.execute("DELETE FROM decomp_run WHERE id = ANY($1)", run_ids)
    finally:
        await conn.close()


@pytest.mark.integration
async def test_run_manifest_round_trips_against_real_postgres() -> None:
    dsn = _asyncpg_dsn(get_settings().database_url)
    engine = make_engine(get_settings().database_url)
    sf = make_sessionmaker(engine)
    store = ProvenanceStore(sf)
    try:
        await _cleanup(dsn)  # in case a prior run left rows behind

        await store.create_run(_RUN_ID, "26.07d", _fingerprint(("C6135",)))
        claim = await store.claim_work_item(_RUN_ID, "C6135")
        assert claim is not None
        await store.complete_work_item(
            _RUN_ID,
            "C6135",
            claim,
            decomposition=Decomposition(
                code="C6135",
                semantic_type="Neoplastic Process",
                constituents=[
                    Constituent(
                        axis="op:PrimarySite",
                        filler_code="C12400",
                        axis_source="role",
                        source_role="R101",
                    )
                ],
            ),
            minted=(),
        )
        assert await store.pending_codes(_RUN_ID) == []
        persisted = await store.decompositions_for_run(_RUN_ID)
        assert persisted[0].constituents[0].axis == "op:PrimarySite"
        assert persisted[0].constituents[0].source_role == "R101"

        finished = await store.finish_run(
            _RUN_ID,
            source_identity="a" * 64,
            metrics={"decomposed": 1, "total_in_scope": 1},
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
    engine = make_engine(get_settings().database_url)
    sf = make_sessionmaker(engine)
    store = ProvenanceStore(sf)
    try:
        await _cleanup(dsn)
        proposal = MintedConcept(axis="op:Laterality", label="Left")
        mint_id = proposal.id
        for run_id in (_RUN_ID, _RERUN_ID):
            await store.create_run(run_id, "26.07d", _fingerprint(("C1",)))
        first_claim = await store.claim_work_item(_RUN_ID, "C1")
        assert first_claim is not None
        await store.complete_work_item(
            _RUN_ID,
            "C1",
            first_claim,
            decomposition=Decomposition(
                code="C1",
                semantic_type="Neoplastic Process",
                constituents=[
                    Constituent(
                        axis="op:Laterality",
                        filler_code=mint_id,
                        axis_source="nlp",
                    )
                ],
            ),
            minted=(proposal,),
        )
        assert await store.finish_run(
            _RUN_ID,
            source_identity="a" * 64,
            metrics={"decomposed": 1, "total_in_scope": 1},
        )

        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(
                "UPDATE minted_concept SET status = 'approved' WHERE id = $1",
                mint_id,
            )
        finally:
            await conn.close()

        second_claim = await store.claim_work_item(_RERUN_ID, "C1")
        assert second_claim is not None
        await store.complete_work_item(
            _RERUN_ID,
            "C1",
            second_claim,
            decomposition=Decomposition(
                code="C1",
                semantic_type="Neoplastic Process",
                constituents=[
                    Constituent(
                        axis="op:Laterality",
                        filler_code=mint_id,
                        axis_source="nlp",
                    )
                ],
            ),
            minted=(proposal,),
        )
        assert await store.finish_run(
            _RERUN_ID,
            source_identity="a" * 64,
            metrics={"decomposed": 1, "total_in_scope": 1},
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
        await _cleanup(dsn)
        await dispose_engine(engine)
