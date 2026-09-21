from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import asyncpg
import pytest

from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.decomposition.mixed_chain_inventory import HistoricalMixedChainRunBinding
from ontolib.decomposition.pre_resume import PRE_RESUME_SQL, acquire_candidate_evidence
from ontolib.decomposition.provenance import (
    ProvenanceStore,
    RunIdentityMismatchError,
    RunStateError,
)
from ontolib.decomposition.provenance_models import (
    RUN_STAGE_SEQUENCE_IDENTITY,
    RunFingerprint,
    RunResumeIdentity,
)
from ontolib.decomposition.resume_dry_run import inspect_resume_selection

pytestmark = [pytest.mark.integration, pytest.mark.mutating_integration]


class _P106Client:
    async def select(self, query: str, *, required_variables=()):
        assert query.lstrip().startswith("PREFIX")
        assert required_variables == {"code", "st"}
        return [{"code": "C12418", "st": "Body Location or Region"}]


async def test_historical_mixed_chain_reader_is_exact_and_fail_closed(
    isolated_postgres_url: str,
) -> None:
    run_id = "neoplasm-2b39c3fc-0ae8-4220-971b-20d861ada722"
    dsn = isolated_postgres_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    fingerprint = {
        "schema_version": 4,
        "source_identity": "a" * 64,
        "collapse_policy_identity": "b" * 64,
        "routing_implementation_identity": "c" * 64,
        "branch": "neoplasm",
        "scope_root": "C3262",
        "scope_version": "stated-genus-subclass-v1",
        "semantic_types": ["Neoplastic Process"],
        "worklist": ["C1"],
        "total_limit": None,
        "sample_manifest_identity": None,
        "algorithm_version": "decomposition-v5",
        "config_version": "nested-definition-v2",
        "walker_max_depth": 7,
        "output_mode": "file",
        "load_mode": "none",
        "emitted_at": "2026-09-08T00:00:00Z",
    }
    fingerprint_identity = hashlib.sha256(
        json.dumps(fingerprint, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute(
            "INSERT INTO decomp_run (id, branch, status, ncit_version, started_at, "
            "finished_at, source_identity, fingerprint, fingerprint_sha256, "
            "emitted_at, publication_state, publication_attempt_count, "
            "representation_identity, publication_artifact_path, "
            "publication_built_at, publication_started_at, publication_finished_at) "
            "VALUES ($1, 'neoplasm', 'running', '26.07d', now(), NULL, $2, "
            "$3::jsonb, $4, now(), 'not_requested', 0, NULL, NULL, NULL, NULL, NULL)",
            run_id,
            "a" * 64,
            json.dumps(fingerprint),
            fingerprint_identity,
        )
        await connection.execute(
            "INSERT INTO decomp_work_item (run_id, concept_code, ordinal, state, "
            "attempt_count, semantic_type, semantic_types, outcome, is_decomposed, "
            "is_residual, has_complete_definition, constituent_count, minted_count, "
            "completed_at) VALUES ($1, 'C1', 0, 'complete', 1, "
            "'Neoplastic Process', '[\"Neoplastic Process\"]'::jsonb, "
            "'atomic-no-op', false, false, false, 0, 0, now())",
            run_id,
        )
    finally:
        await connection.close()

    engine = make_engine(isolated_postgres_url)
    try:
        store = ProvenanceStore(make_sessionmaker(engine))
        with pytest.raises(RunStateError, match="not complete and published"):
            await store.historical_mixed_chain_run_for_evidence(run_id)

        connection = await asyncpg.connect(dsn)
        try:
            await connection.execute(
                "UPDATE decomp_run SET status='complete', finished_at=now(), "
                "publication_state='published', publication_attempt_count=1, "
                "representation_identity=$2, publication_artifact_path='tmp/old.ttl', "
                "publication_built_at=now(), publication_started_at=now(), "
                "publication_finished_at=now() WHERE id=$1",
                run_id,
                "d" * 64,
            )
        finally:
            await connection.close()

        observed = await store.historical_mixed_chain_run_for_evidence(run_id)
        assert isinstance(observed, HistoricalMixedChainRunBinding)
        assert observed.fingerprint_identity == fingerprint_identity
        assert observed.materialized_worklist == ("C1",)

        with pytest.raises(RunStateError, match="does not exist"):
            await store.historical_mixed_chain_run_for_evidence("missing-run")

        connection = await asyncpg.connect(dsn)
        try:
            await connection.execute(
                "UPDATE decomp_work_item SET concept_code='C2' WHERE run_id=$1",
                run_id,
            )
        finally:
            await connection.close()
        with pytest.raises(RunIdentityMismatchError, match="exact schema"):
            await store.historical_mixed_chain_run_for_evidence(run_id)
    finally:
        await dispose_engine(engine)


async def test_disposable_postgres_candidate_shape_preserves_source_occurrences(
    isolated_postgres_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dsn = isolated_postgres_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    connection = await asyncpg.connect(dsn)
    digest = "a" * 64
    try:
        await connection.execute(
            "INSERT INTO decomp_run (id, branch, status, ncit_version, started_at, "
            "source_identity, fingerprint, fingerprint_sha256, emitted_at, error_type, "
            "error_message, publication_state) VALUES ('proof-test', 'neoplasm', "
            "'failed', '26.07d', now(), $1, '{}'::jsonb, $1, now(), 'Test', 'test', "
            "'not_requested')",
            digest,
        )
        await connection.execute(
            "INSERT INTO decomp_work_item (run_id, concept_code, ordinal, state, "
            "attempt_count, semantic_type, semantic_types, outcome, is_decomposed, "
            "is_residual, has_complete_definition, constituent_count, minted_count, "
            "completed_at) VALUES ('proof-test', 'C1', 0, 'complete', 1, "
            "'Neoplastic Process', '[\"Neoplastic Process\"]'::jsonb, 'decomposed', "
            "true, false, true, 1, 0, now())"
        )
        await connection.execute(
            "INSERT INTO decomp_work_item (run_id, concept_code, ordinal) "
            "VALUES ('proof-test', 'C2', 1)"
        )
        await connection.execute(
            "INSERT INTO decomp_constituent (run_id, concept_code, axis, filler_code, "
            "axis_source, source_roles, most_specific, needs_review, "
            "source_definition_ids, source_group_ids) VALUES "
            "('proof-test', 'C1', 'op:Morphology', 'C3878', 'parent', "
            "'[]'::jsonb, false, false, '[]'::jsonb, '[]'::jsonb)"
        )
        await connection.execute(
            "INSERT INTO decomp_constituent (run_id, concept_code, axis, filler_code, "
            "axis_source, source_roles, most_specific, needs_review, "
            "source_definition_ids, source_group_ids) VALUES "
            "('proof-test', 'C2', 'op:Morphology', 'C3878', 'parent', "
            "'[]'::jsonb, false, false, '[]'::jsonb, '[]'::jsonb)"
        )
        for concept_offset, concept_code in enumerate(("C1", "C2")):
            for index, filler in enumerate(("C12400", "C12418"), start=1):
                identity_seed = f"{concept_offset}:{index}"
                group_id = hashlib.sha256(f"group:{identity_seed}".encode()).hexdigest()
                fact_id = hashlib.sha256(f"fact:{identity_seed}".encode()).hexdigest()
                occurrence_id = hashlib.sha256(
                    f"occurrence:{identity_seed}".encode()
                ).hexdigest()
                await connection.execute(
                    "INSERT INTO decomp_definition_group VALUES "
                    "('proof-test', $1, $2, 'C3878', 1, true)",
                    concept_code,
                    group_id,
                )
                await connection.execute(
                    "INSERT INTO decomp_definition_fact (run_id, concept_code, "
                    "fact_id, anchor_code, group_id, depth, fact_kind, role_code, "
                    "filler_code) VALUES ('proof-test', $1, $2, 'C3878', $3, 1, "
                    "'restriction', 'R101', $4)",
                    concept_code,
                    fact_id,
                    group_id,
                    filler,
                )
                await connection.execute(
                    "INSERT INTO decomp_source_occurrence VALUES "
                    "('proof-test', $1, $2, $3, $4, 'C3878', 1, 'R101', $5, "
                    "ARRAY[$6]::integer[], $6)",
                    concept_code,
                    occurrence_id,
                    fact_id,
                    group_id,
                    filler,
                    index,
                )
    finally:
        await connection.close()

    engine = make_engine(isolated_postgres_url)
    try:
        evidence = await acquire_candidate_evidence(engine, "proof-test", _P106Client())
        monkeypatch.setitem(
            PRE_RESUME_SQL,
            "candidates",
            PRE_RESUME_SQL["candidates"].replace("AND w.state = 'complete' ", ""),
        )
        pending_admitted = await acquire_candidate_evidence(
            engine, "proof-test", _P106Client()
        )
    finally:
        await dispose_engine(engine)

    assert evidence.production.counts == (1, 1, 1, 1)
    assert {item.concept_code for item in evidence.production.tuples} == {"C1"}
    assert pending_admitted.production.counts == (2, 2, 2, 1)
    assert evidence.validation.authorizable is True


async def test_production_resume_preview_is_read_only_at_exact_protected_scale(
    isolated_postgres_url: str,
) -> None:
    dsn = isolated_postgres_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    codes = tuple(f"C{index:05d}" for index in range(15633))
    fingerprint = RunFingerprint(
        source_identity="a" * 64,
        collapse_policy_identity="0" * 64,
        routing_implementation_identity="1" * 64,
        mixed_chain_inventory_identity="2" * 64,
        stage_sequence_identity=RUN_STAGE_SEQUENCE_IDENTITY,
        branch="neoplasm",
        scope_root="C3262",
        scope_version="stated-genus-subclass-v1",
        semantic_types=(
            "Cell or Molecular Dysfunction",
            "Disease or Syndrome",
            "Neoplastic Process",
        ),
        worklist=codes,
        algorithm_version="decomposition-v4",
        config_version="nested-definition-v2",
        walker_max_depth=7,
        output_mode="file",
        load_mode="none",
        emitted_at=datetime(2026, 8, 16, tzinfo=UTC),
    )
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute(
            "INSERT INTO decomp_run (id, branch, status, ncit_version, started_at, "
            "source_identity, fingerprint, fingerprint_sha256, emitted_at, error_type, "
            "error_message, publication_state) VALUES ('resume-preview', 'neoplasm', "
            "'failed', '26.07d', now(), $1, $2::jsonb, $3, now(), "
            "'BrokenPipeError', '[Errno 32] Broken pipe', 'not_requested')",
            fingerprint.source_identity,
            fingerprint.model_dump_json(),
            fingerprint.identity,
        )
        await connection.execute(
            "INSERT INTO decomp_work_item (run_id, concept_code, ordinal) "
            "SELECT 'resume-preview', 'C' || lpad(i::text, 5, '0'), i "
            "FROM generate_series(0, 15632) AS i"
        )
        await connection.execute(
            "UPDATE decomp_work_item SET state = 'complete', attempt_count = 1, "
            "semantic_type = 'Neoplastic Process', "
            "semantic_types = '[\"Neoplastic Process\"]'::jsonb, "
            "outcome = 'atomic-no-op', is_decomposed = false, is_residual = false, "
            "has_complete_definition = false, constituent_count = 0, minted_count = 0, "
            "completed_at = now() WHERE run_id = 'resume-preview' AND ordinal < 5900"
        )
        before = await connection.fetchval(
            "SELECT jsonb_build_object('run', (SELECT to_jsonb(r) FROM decomp_run r "
            "WHERE id = 'resume-preview'), 'items', (SELECT jsonb_agg(to_jsonb(w) "
            "ORDER BY ordinal) FROM decomp_work_item w "
            "WHERE run_id = 'resume-preview'))"
        )
    finally:
        await connection.close()

    engine = make_engine(isolated_postgres_url)
    try:
        selection, failure = await inspect_resume_selection(
            engine,
            "resume-preview",
            RunResumeIdentity.from_fingerprint(fingerprint),
        )
    finally:
        await dispose_engine(engine)

    connection = await asyncpg.connect(dsn)
    try:
        after = await connection.fetchval(
            "SELECT jsonb_build_object('run', (SELECT to_jsonb(r) FROM decomp_run r "
            "WHERE id = 'resume-preview'), 'items', (SELECT jsonb_agg(to_jsonb(w) "
            "ORDER BY ordinal) FROM decomp_work_item w "
            "WHERE run_id = 'resume-preview'))"
        )
    finally:
        await connection.close()

    assert len(selection.completed_codes) == 5900
    assert len(selection.pending_codes) == 9733
    assert selection.selected_complete_count == 0
    assert selection.postgres_reads == 3
    assert failure == ("failed", "BrokenPipeError", "[Errno 32] Broken pipe")
    assert json.loads(before) == json.loads(after)
