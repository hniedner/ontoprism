"""Real-PostgreSQL authority contracts for decomposition run admission."""

from __future__ import annotations

import asyncio
import datetime
from pathlib import Path

import asyncpg
import pytest

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.decomposition.provenance import ProvenanceStore
from ontolib.decomposition.provenance_models import (
    RUN_STAGE_SEQUENCE_IDENTITY,
    FreshAdmitted,
    FullRunExecutionIdentity,
    RefusalReason,
    Refused,
    ResumeAdmitted,
    ResumeKind,
    RunFingerprint,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.mutating_integration,
    pytest.mark.usefixtures("isolated_postgres_settings"),
]


def _execution() -> FullRunExecutionIdentity:
    return FullRunExecutionIdentity(
        source_identity="a" * 64,
        worklist=("C1",),
        branch="neoplasm",
        scope_root="C3262",
        scope_version="stated-genus-subclass-v1",
        semantic_types=("Neoplastic Process",),
        algorithm_version="decomposition-v3",
        config_version="nested-definition-v2",
        walker_max_depth=5,
        routing_implementation_identity="b" * 64,
        collapse_policy_identity="c" * 64,
        mixed_chain_inventory_identity="d" * 64,
        stage_sequence_identity=RUN_STAGE_SEQUENCE_IDENTITY,
        output_mode="none",
        load_mode="none",
    )


def _fingerprint(execution: FullRunExecutionIdentity) -> RunFingerprint:
    return RunFingerprint(
        **execution.model_dump(
            exclude={
                "schema_version",
                "stage_sequence_identity",
                "mixed_chain_inventory_identity",
            }
        ),
        mixed_chain_inventory_identity=execution.mixed_chain_inventory_identity,
        stage_sequence_identity=execution.stage_sequence_identity,
        emitted_at=datetime.datetime.now(datetime.UTC),
    )


async def test_concurrent_admission_creates_exactly_one_authoritative_run() -> None:
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    execution = _execution()
    fingerprint = _fingerprint(execution)
    run_ids = ("admission-concurrent-a", "admission-concurrent-b")
    try:
        outcomes = await asyncio.gather(
            *(
                store.admit_run(run_id, "26.07d", fingerprint, execution)
                for run_id in run_ids
            )
        )
        assert sum(isinstance(outcome, FreshAdmitted) for outcome in outcomes) == 1
        assert [
            outcome.reason for outcome in outcomes if isinstance(outcome, Refused)
        ] == [RefusalReason.ACTIVE_RUN_EXISTS]
        connection = await asyncpg.connect(
            get_settings().database_url.replace("+asyncpg", "")
        )
        try:
            assert (
                await connection.fetchval(
                    "SELECT count(*) FROM decomp_run WHERE execution_identity=$1",
                    execution.identity,
                )
                == 1
            )
        finally:
            await connection.close()
    finally:
        connection = await asyncpg.connect(
            get_settings().database_url.replace("+asyncpg", "")
        )
        try:
            await connection.execute(
                "DELETE FROM decomp_run WHERE id=ANY($1)", list(run_ids)
            )
        finally:
            await connection.close()
        await dispose_engine(engine)


async def test_rehearsals_with_identical_content_are_each_admitted() -> None:
    """A rehearsal (the CLI preflight) must be repeatable on unchanged input."""
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    run_ids = ["admission-rehearsal-1", "admission-rehearsal-2"]
    first = _execution().model_copy(update={"rehearsal_nonce": "1" * 32})
    second = _execution().model_copy(update={"rehearsal_nonce": "2" * 32})
    try:
        assert isinstance(
            await store.admit_run(run_ids[0], "26.07d", _fingerprint(first), first),
            FreshAdmitted,
        )
        assert isinstance(
            await store.admit_run(run_ids[1], "26.07d", _fingerprint(second), second),
            FreshAdmitted,
        )
        summaries = {item.id: item for item in await store.list_runs(limit=10)}
        assert summaries[run_ids[0]].rehearsal is True
    finally:
        connection = await asyncpg.connect(
            get_settings().database_url.replace("+asyncpg", "")
        )
        try:
            await connection.execute(
                "DELETE FROM decomp_run WHERE id=ANY($1)", list(run_ids)
            )
        finally:
            await connection.close()
        await dispose_engine(engine)


async def test_database_index_is_live_against_direct_duplicate_insert() -> None:
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    execution = _execution()
    fingerprint = _fingerprint(execution)
    run_id = "admission-index-live"
    try:
        assert isinstance(
            await store.admit_run(run_id, "26.07d", fingerprint, execution),
            FreshAdmitted,
        )
        connection = await asyncpg.connect(
            get_settings().database_url.replace("+asyncpg", "")
        )
        try:
            with pytest.raises(asyncpg.UniqueViolationError):
                await connection.execute(
                    "INSERT INTO decomp_run "
                    "(id,branch,status,ncit_version,started_at,source_identity,"
                    "fingerprint,fingerprint_sha256,emitted_at,publication_state,"
                    "execution_identity) SELECT 'admission-index-duplicate',branch,"
                    "status,ncit_version,started_at,source_identity,fingerprint,"
                    "fingerprint_sha256,emitted_at,publication_state,"
                    "execution_identity FROM decomp_run WHERE id=$1",
                    run_id,
                )
        finally:
            await connection.close()
    finally:
        connection = await asyncpg.connect(
            get_settings().database_url.replace("+asyncpg", "")
        )
        try:
            await connection.execute(
                "DELETE FROM decomp_run WHERE id=ANY($1)",
                [run_id, "admission-index-duplicate"],
            )
        finally:
            await connection.close()
        await dispose_engine(engine)


async def test_refusal_reasons_and_exact_resume_paths_are_live() -> None:
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    connection = await asyncpg.connect(
        get_settings().database_url.replace("+asyncpg", "")
    )
    run_ids = [
        "admission-active",
        "admission-stage-damaged",
        "admission-complete",
        "admission-publication",
    ]
    execution = _execution()
    fingerprint = _fingerprint(execution)
    try:
        assert isinstance(
            await store.admit_run(run_ids[0], "26.07d", fingerprint, execution),
            FreshAdmitted,
        )
        active = await store.admit_run(
            "unused-active", "26.07d", fingerprint, execution
        )
        assert active == Refused(reason=RefusalReason.ACTIVE_RUN_EXISTS)
        resumed = await store.admit_run(
            "unused-resume",
            "26.07d",
            fingerprint,
            execution,
            resume_run_id=run_ids[0],
        )
        assert resumed == ResumeAdmitted(
            run_id=run_ids[0], resume_kind=ResumeKind.SEMANTIC
        )

        changed_source = execution.model_copy(update={"source_identity": "e" * 64})
        source_drift = await store.admit_run(
            "unused-source",
            "26.07d",
            _fingerprint(changed_source),
            changed_source,
            resume_run_id=run_ids[0],
        )
        assert source_drift == Refused(reason=RefusalReason.SOURCE_DRIFT)

        changed_config = execution.model_copy(update={"walker_max_depth": 6})
        mismatch = await store.admit_run(
            "unused-identity",
            "26.07d",
            _fingerprint(changed_config),
            changed_config,
            resume_run_id=run_ids[0],
        )
        assert mismatch == Refused(reason=RefusalReason.IDENTITY_MISMATCH)

        assert await store.fail_run(run_ids[0], RuntimeError("semantic work stopped"))
        failed_resume = await store.admit_run(
            "unused-failed-resume",
            "26.07d",
            fingerprint,
            execution,
            resume_run_id=run_ids[0],
        )
        assert failed_resume == ResumeAdmitted(
            run_id=run_ids[0], resume_kind=ResumeKind.SEMANTIC
        )
        assert await store.claim_work_item(run_ids[0], "C1") is not None, (
            "a resumed failed run is running again and its work is claimable"
        )

        assert isinstance(
            await store.admit_run(
                run_ids[1],
                "26.07d",
                _fingerprint(changed_config),
                changed_config,
            ),
            FreshAdmitted,
        )
        await connection.execute(
            "DELETE FROM decomp_run_stage WHERE run_id=$1 AND stage='publication'",
            run_ids[1],
        )
        damaged = await store.admit_run(
            "unused-stage",
            "26.07d",
            _fingerprint(changed_config),
            changed_config,
            resume_run_id=run_ids[1],
        )
        assert damaged == Refused(reason=RefusalReason.STAGE_SCHEMA_MISMATCH)

        empty = execution.model_copy(update={"worklist": (), "walker_max_depth": 7})
        empty_fingerprint = _fingerprint(empty)
        assert isinstance(
            await store.admit_run(run_ids[2], "26.07d", empty_fingerprint, empty),
            FreshAdmitted,
        )
        assert await store.finish_run(
            run_ids[2],
            source_identity=empty.source_identity,
            metrics={
                "total_in_scope": 0,
                "decomposed": 0,
                "residual": 0,
                "semantic_excluded": 0,
                "atomic_noop": 0,
                "unknown_outcome": 0,
                "residual_precoordinated_count": 0,
                "residual_precoordination_unknown_count": 0,
                "residual_precoordination": 0.0,
                "minted_count": 0,
                "complete_definition_count": 0,
                "complete_fact_count": 0,
                "projected_fact_count": 0,
                "projection_loss_count": 0,
                "projection_loss_rate": 0.0,
                "pct_decomposed": 0.0,
                "roundtrip_fidelity": None,
            },
        )
        completed = await store.admit_run(
            "unused-complete", "26.07d", empty_fingerprint, empty
        )
        assert completed == Refused(reason=RefusalReason.COMPLETED_RUN_EXISTS)
        explicit_completed_resume = await store.admit_run(
            "unused-complete-resume",
            "26.07d",
            empty_fingerprint,
            empty,
            resume_run_id=run_ids[2],
        )
        assert explicit_completed_resume == Refused(
            reason=RefusalReason.COMPLETED_RUN_EXISTS
        )

        publication = empty.model_copy(
            update={
                "walker_max_depth": 8,
                "output_mode": "file",
                "load_mode": "none",
            }
        )
        publication_fingerprint = _fingerprint(publication)
        assert isinstance(
            await store.admit_run(
                run_ids[3], "26.07d", publication_fingerprint, publication
            ),
            FreshAdmitted,
        )
        await store.begin_publication(
            run_ids[3],
            representation_identity="f" * 64,
            artifact_path=str(Path.cwd() / "tmp/admission.ttl"),
            built_at=datetime.datetime.now(datetime.UTC),
            predecessor=None,
        )
        await store.record_publication_failure(
            run_ids[3], RuntimeError("publication stopped")
        )
        retry = await store.admit_run(
            "unused-publication",
            "26.07d",
            publication_fingerprint,
            publication,
        )
        assert retry == Refused(reason=RefusalReason.PUBLICATION_RETRY_REQUIRED)
        publication_resume = await store.admit_run(
            "unused-publication-resume",
            "26.07d",
            publication_fingerprint,
            publication,
            resume_run_id=run_ids[3],
        )
        assert publication_resume == ResumeAdmitted(
            run_id=run_ids[3], resume_kind=ResumeKind.PUBLICATION
        )
    finally:
        await connection.execute("DELETE FROM decomp_run WHERE id=ANY($1)", run_ids)
        await connection.close()
        await dispose_engine(engine)


async def test_multiple_compatible_rows_fail_closed_when_constraint_is_mutated() -> (
    None
):
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    connection = await asyncpg.connect(
        get_settings().database_url.replace("+asyncpg", "")
    )
    execution = _execution()
    fingerprint = _fingerprint(execution)
    run_ids = ["admission-ambiguous-a", "admission-ambiguous-b"]
    try:
        assert isinstance(
            await store.admit_run(run_ids[0], "26.07d", fingerprint, execution),
            FreshAdmitted,
        )
        await connection.execute("DROP INDEX uq_decomp_run_admitted_execution")
        await connection.execute(
            "INSERT INTO decomp_run "
            "(id,branch,status,ncit_version,started_at,source_identity,fingerprint,"
            "fingerprint_sha256,emitted_at,publication_state,execution_identity) "
            "SELECT $2,branch,status,ncit_version,started_at,source_identity,"
            "fingerprint,fingerprint_sha256,emitted_at,publication_state,"
            "execution_identity FROM decomp_run WHERE id=$1",
            run_ids[0],
            run_ids[1],
        )
        outcome = await store.admit_run(
            "unused-ambiguous", "26.07d", fingerprint, execution
        )
        assert outcome == Refused(reason=RefusalReason.AMBIGUOUS_COMPATIBLE_RUNS)
    finally:
        await connection.execute("DELETE FROM decomp_run WHERE id=ANY($1)", run_ids)
        await connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_decomp_run_admitted_execution "
            "ON decomp_run (execution_identity) WHERE execution_identity IS NOT NULL "
            "AND status IN ('running', 'failed', 'complete')"
        )
        await connection.close()
        await dispose_engine(engine)
