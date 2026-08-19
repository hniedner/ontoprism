from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import asyncpg
import pytest

from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.decomposition.pre_resume import PRE_RESUME_SQL, acquire_candidate_evidence
from ontolib.decomposition.provenance import ProvenanceStore
from ontolib.decomposition.provenance_models import RunFingerprint, RunResumeIdentity
from ontolib.decomposition.r101_conservation import (
    STRUCTURAL_KEY_FIELDS,
    LedgerBuildContext,
    Pair,
    QueryMetrics,
    R101ConservationValidationError,
    build_r101_occurrence_ledger,
    r101_detector_identity,
    r101_proof_identity,
    validate_r101_consumer_dry_run,
)
from ontolib.decomposition.resume_dry_run import inspect_resume_selection


class _P106Client:
    async def select(self, query: str, *, required_variables=()):
        assert query.lstrip().startswith("PREFIX")
        assert required_variables == {"code", "st"}
        return [{"code": "C12418", "st": "Body Location or Region"}]


@pytest.mark.integration
@pytest.mark.mutating_integration
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
            "source_definition_ids) VALUES ('proof-test', 'C1', 'op:Morphology', "
            "'C3878', 'parent', '[]'::jsonb, false, false, '[]'::jsonb)"
        )
        await connection.execute(
            "INSERT INTO decomp_constituent (run_id, concept_code, axis, filler_code, "
            "axis_source, source_roles, most_specific, needs_review, "
            "source_definition_ids) VALUES ('proof-test', 'C2', 'op:Morphology', "
            "'C3878', 'parent', '[]'::jsonb, false, false, '[]'::jsonb)"
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


@pytest.mark.integration
@pytest.mark.mutating_integration
async def test_production_resume_preview_is_read_only_at_exact_protected_scale(
    isolated_postgres_url: str,
) -> None:
    dsn = isolated_postgres_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    codes = tuple(f"C{index:05d}" for index in range(15633))
    fingerprint = RunFingerprint(
        source_identity="a" * 64,
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


@pytest.mark.integration
@pytest.mark.mutating_integration
async def test_r101_candidate_query_preserves_old_and_new_occurrence_origins(
    isolated_postgres_url: str,
) -> None:
    dsn = isolated_postgres_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    connection = await asyncpg.connect(dsn)
    occurrence_id = "a" * 64
    try:
        for run_id in ("old-r101", "new-r101"):
            await connection.execute(
                "INSERT INTO decomp_run (id, branch, status, ncit_version, started_at, "
                "source_identity, fingerprint, fingerprint_sha256, emitted_at, "
                "publication_state) VALUES ($1, 'neoplasm', 'running', "
                "'26.07d', now(), "
                "$2, '{}'::jsonb, $2, now(), 'not_requested')",
                run_id,
                "b" * 64,
            )
            await connection.execute(
                "INSERT INTO decomp_work_item (run_id, concept_code, ordinal) "
                "VALUES ($1, 'C1', 0)",
                run_id,
            )
            await connection.execute(
                "INSERT INTO decomp_definition_group VALUES "
                "($1, 'C1', $2, 'C1', 1, true)",
                run_id,
                "d" * 64,
            )
            await connection.execute(
                "INSERT INTO decomp_definition_fact (run_id, concept_code, fact_id, "
                "anchor_code, group_id, depth, fact_kind, role_code, filler_code) "
                "VALUES ($1, 'C1', $2, 'C1', $3, 1, 'restriction', 'R101', 'C10')",
                run_id,
                "c" * 64,
                "d" * 64,
            )
            await connection.execute(
                "INSERT INTO decomp_source_occurrence VALUES "
                "($1, 'C1', $2, $3, $4, 'C1', 1, 'R101', 'C10', "
                "ARRAY[0]::integer[], 0)",
                run_id,
                occurrence_id,
                "c" * 64,
                "d" * 64,
            )
        run_axes = (
            ("old-r101", "op:PrimarySite"),
            ("new-r101", "op:AssociatedRegion"),
        )
        for run_id, axis in run_axes:
            await connection.execute(
                "INSERT INTO decomp_constituent "
                "(run_id, concept_code, axis, filler_code, axis_source, source_roles, "
                "most_specific, needs_review, source_definition_ids) "
                "VALUES ($1, 'C1', $2, 'C10', 'role', '[\"R101\"]'::jsonb, "
                "true, false, '[]'::jsonb)",
                run_id,
                axis,
            )
            await connection.execute(
                "INSERT INTO decomp_constituent_occurrence "
                "(run_id, concept_code, axis, filler_code, occurrence_id) "
                "VALUES ($1, 'C1', $2, 'C10', $3)",
                run_id,
                axis,
                occurrence_id,
            )
        for run_id, filler_code in (
            ("old-r101", "C20"),
            ("new-r101", "C21"),
        ):
            await connection.execute(
                "INSERT INTO decomp_constituent "
                "(run_id, concept_code, axis, filler_code, axis_source, source_roles, "
                "most_specific, needs_review, source_definition_ids) "
                "VALUES ($1, 'C1', 'op:Morphology', $2, 'parent', '[]'::jsonb, "
                "true, false, '[]'::jsonb)",
                run_id,
                filler_code,
            )
    finally:
        await connection.close()

    engine = make_engine(isolated_postgres_url)
    try:
        store = ProvenanceStore(make_sessionmaker(engine))
        ledger = await store.r101_occurrence_ledger("old-r101", "new-r101")
    finally:
        await dispose_engine(engine)

    assert len(ledger.occurrences) == 1
    item = ledger.occurrences[0]
    assert item.old_links == (Pair(axis="op:PrimarySite", filler_code="C10"),)
    assert item.new_links == (Pair(axis="op:AssociatedRegion", filler_code="C10"),)
    assert item.retained_new_r101_links == item.new_links
    assert tuple(
        getattr(item.old_occurrence, field) for field in STRUCTURAL_KEY_FIELDS
    ) == tuple(getattr(item.new_occurrence, field) for field in STRUCTURAL_KEY_FIELDS)
    assert item.old_occurrence.source_fact_id == "c" * 64
    assert item.old_occurrence.source_group_id == "d" * 64
    assert item.old_occurrence.structural_path == (0,)
    assert item.old_occurrence.member_position == 0
    assert [row.model_dump() for row in ledger.non_r101_delta_evidence.rows] == [
        {
            "change": "added",
            "concept_code": "C1",
            "axis": "op:Morphology",
            "filler_code": "C21",
        },
        {
            "change": "removed",
            "concept_code": "C1",
            "axis": "op:Morphology",
            "filler_code": "C20",
        },
    ]
    assert ledger.non_r101_delta_evidence.old_run_id == "old-r101"
    assert ledger.non_r101_delta_evidence.new_run_id == "new-r101"

    prerequisite_ids = ("1" * 64, "2" * 64, "3" * 64)
    context = LedgerBuildContext(
        source_identity="b" * 64,
        source_release_id="26.07d",
        old_run_id="old-r101",
        old_run_fingerprint_identity="4" * 64,
        old_representation_identity="5" * 64,
        old_baseline_identity="6" * 64,
        new_run_id="new-r101",
        new_run_fingerprint_identity="7" * 64,
        new_representation_identity="8" * 64,
        detector_identity=r101_detector_identity(),
        pre_resume_proof_identity=prerequisite_ids[0],
        resume_dry_run_identity=prerequisite_ids[1],
        mixed_cohort_identity=prerequisite_ids[2],
        proof_identity=r101_proof_identity(*prerequisite_ids),
        adapter_id="ncit-stated-r82-v1",
        query_metrics=QueryMetrics(
            postgres_query_count=3,
            qlever_query_count=0,
            max_pair_batch_size=0,
            max_r82_hops=8,
            max_asserted_superclass_hops=20,
        ),
        non_r101_delta_evidence=ledger.non_r101_delta_evidence,
    )
    report = build_r101_occurrence_ledger(
        ledger.occurrences,
        paths={},
        context=context,
    )
    assert await validate_r101_consumer_dry_run(report, store) == report.json_identity

    empty = build_r101_occurrence_ledger((), paths={}, context=context)
    with pytest.raises(R101ConservationValidationError, match="inventory"):
        await validate_r101_consumer_dry_run(empty, store)
