"""Integration tests for ProvenanceStore against a real Postgres (design §4.5).

Every existing unit test in ``test_provenance.py`` mocks the session entirely — none
of the raw SQL (composite-key ``ON CONFLICT``, the FK to ``decomp_run``, the ``jsonb``
metrics column) has ever run against a real database. This round-trips the store
against a run-owned disposable Postgres database and cleans up exactly. Fails when
Postgres is unreachable.
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
from pathlib import Path

import asyncpg
import pytest
from pydantic import ValidationError
from scripts.research.current_evidence import generate_current_evidence
from sqlalchemy import event

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.decomposition import provenance as provenance_module
from ontolib.decomposition.minting import MintedConcept
from ontolib.decomposition.models import (
    CompleteDefinition,
    Constituent,
    Decomposition,
    DefinitionGroup,
    RestrictionDefinitionFact,
    SourceDefinitionOccurrence,
    canonical_definition_fact_id,
    canonical_definition_group_id,
    canonical_source_occurrence_id,
)
from ontolib.decomposition.provenance import (
    ProvenanceStore,
    RunIdentityMismatchError,
    RunStateError,
)
from ontolib.decomposition.provenance_models import RunFingerprint, RunResumeIdentity
from ontolib.decomposition.sampling import load_sample_manifest

_RUN_ID = "test-provenance-integration-run"
_RERUN_ID = "test-provenance-integration-rerun"
_PUBLICATION_RUN_ID = "test-provenance-publication-run"
_CURRENT_EVIDENCE_RUN_ID = "test-current-evidence-generator-run"
_CURRENT_MANIFEST = Path("samples/ncit-26.07d-m1-current-replay.json")
_CURRENT_GOLDEN = Path(__file__).parent / "golden"

pytestmark = [
    pytest.mark.mutating_integration,
    pytest.mark.usefixtures("isolated_postgres_settings"),
]


def _asyncpg_dsn(sqlalchemy_url: str) -> str:
    return sqlalchemy_url.replace("+asyncpg", "")


def _fingerprint(worklist: tuple[str, ...]) -> RunFingerprint:
    return RunFingerprint(
        source_identity="a" * 64,
        collapse_policy_identity="0" * 64,
        branch="neoplasm",
        scope_root="C3262",
        scope_version="stated-genus-subclass-v1",
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


def _publication_fingerprint() -> RunFingerprint:
    return _fingerprint(()).model_copy(
        update={"output_mode": "file", "load_mode": "named-graph"}
    )


async def _cleanup(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        run_ids = [
            _RUN_ID,
            _RERUN_ID,
            _PUBLICATION_RUN_ID,
            _CURRENT_EVIDENCE_RUN_ID,
            _SHARED_GENUS_RUN_ID,
        ]
        await conn.execute(
            "DELETE FROM decomp_constituent WHERE run_id = ANY($1)", run_ids
        )
        await conn.execute("DELETE FROM minted_concept WHERE run_id = ANY($1)", run_ids)
        await conn.execute("DELETE FROM decomp_run WHERE id = ANY($1)", run_ids)
    finally:
        await conn.close()


async def _completion_metrics(
    store: ProvenanceStore,
    run_id: str,
) -> dict[str, object]:
    counts = await store.outcome_counts(run_id)
    decompositions = await store.decompositions_for_run(run_id)
    complete_fact_count = sum(item.complete_fact_count for item in decompositions)
    projected_fact_count = sum(item.projected_fact_count for item in decompositions)
    projection_loss_count = complete_fact_count - projected_fact_count
    return {
        **counts.model_dump(),
        "residual_precoordinated_count": 0,
        "residual_precoordination": 0.0,
        "complete_definition_count": sum(
            item.complete_definition is not None for item in decompositions
        ),
        "complete_fact_count": complete_fact_count,
        "projected_fact_count": projected_fact_count,
        "projection_loss_count": projection_loss_count,
        "projection_loss_rate": (
            projection_loss_count / complete_fact_count if complete_fact_count else 0.0
        ),
        "pct_decomposed": (
            counts.decomposed / counts.total_in_scope if counts.total_in_scope else 0.0
        ),
        "roundtrip_fidelity": None,
    }


async def _assert_processing_and_publication_failures_remain_separate(
    store: ProvenanceStore,
) -> None:
    await store.record_publication_failure(
        _PUBLICATION_RUN_ID,
        RuntimeError("publication unavailable " + "x" * 2000),
    )
    failed = await store.get_run(_PUBLICATION_RUN_ID)
    assert failed is not None
    assert failed.status == "running"
    assert failed.error_type is None
    assert failed.error_message is None
    assert failed.publication_state == "failed"
    assert failed.publication_error_type == "RuntimeError"
    assert failed.publication_error_message is not None
    assert len(failed.publication_error_message) == 1000

    assert await store.fail_run(
        _PUBLICATION_RUN_ID,
        RuntimeError("reconstruction also unavailable"),
    )
    doubly_failed = await store.get_run(_PUBLICATION_RUN_ID)
    assert doubly_failed is not None
    assert doubly_failed.status == "failed"
    assert doubly_failed.error_type == "RuntimeError"
    assert doubly_failed.publication_state == "failed"
    assert doubly_failed.publication_error_type == "RuntimeError"

    await store.resume_run(
        _PUBLICATION_RUN_ID,
        RunResumeIdentity.from_fingerprint(_publication_fingerprint()),
    )
    resumed = await store.get_run(_PUBLICATION_RUN_ID)
    assert resumed is not None
    assert resumed.status == "running"
    assert resumed.error_type is None
    assert resumed.publication_state == "failed"
    assert resumed.publication_error_type == "RuntimeError"


_SHARED_GENUS_RUN_ID = "test-provenance-shared-genus-run"


def _shared_genus_decomposition(root_code: str, depth: int) -> Decomposition:
    """Two roots that walk into the SAME defined genus.

    ``canonical_definition_fact_id`` is anchored on the expression's own concept,
    not the root, so both roots legitimately cite the identical ``fact_id``.
    """
    group_id = canonical_definition_group_id("C100", ("restriction:R101:C200",))
    fact_id = canonical_definition_fact_id(
        "C100", group_id, "restriction", "R101", "C200"
    )
    return Decomposition(
        code=root_code,
        semantic_type="Neoplastic Process",
        constituents=(
            Constituent(
                axis="R101",
                filler_code="C200",
                axis_source="role",
                source_roles=("R101",),
                source_definition_ids=(fact_id,),
            ),
        ),
        complete_definition=CompleteDefinition(
            root_code=root_code,
            facts=(
                RestrictionDefinitionFact(
                    fact_id=fact_id,
                    anchor_code="C100",
                    group_id=group_id,
                    depth=depth,
                    role_code="R101",
                    filler_code="C200",
                ),
            ),
            groups=(
                DefinitionGroup(group_id=group_id, anchor_code="C100", depth=depth),
            ),
            root_group_ids=(group_id,),
        ),
    )


def _repeated_occurrence_decomposition() -> Decomposition:
    group_id = canonical_definition_group_id("C6135", ("restriction:R101:C12400",))
    fact_id = canonical_definition_fact_id(
        "C6135", group_id, "restriction", "R101", "C12400"
    )
    occurrences = tuple(
        SourceDefinitionOccurrence(
            occurrence_id=canonical_source_occurrence_id(
                "C6135", fact_id, (0, position)
            ),
            root_code="C6135",
            source_fact_id=fact_id,
            source_group_id=group_id,
            anchor_code="C6135",
            depth=0,
            role_code="R101",
            filler_code="C12400",
            structural_path=(0, position),
            member_position=position,
        )
        for position in (0, 1)
    )
    return Decomposition(
        code="C6135",
        semantic_type="Neoplastic Process",
        constituents=(
            Constituent(
                axis="op:PrimarySite",
                filler_code="C12400",
                axis_source="role",
                source_roles=("R101",),
                source_definition_ids=(fact_id,),
                source_occurrence_ids=tuple(
                    occurrence.occurrence_id for occurrence in occurrences
                ),
            ),
        ),
        complete_definition=CompleteDefinition(
            root_code="C6135",
            facts=(
                RestrictionDefinitionFact(
                    fact_id=fact_id,
                    anchor_code="C6135",
                    group_id=group_id,
                    depth=0,
                    role_code="R101",
                    filler_code="C12400",
                ),
            ),
            occurrences=occurrences,
        ),
    )


def _residual_decomposition(code: str) -> Decomposition:
    """A residual concept: a complete definition, but no surviving constituents.

    The detector flagged it pre-coordinated, so it carries >= 1 persisted fact,
    yet ``decompositions_for_run`` excludes it because it is not ``is_decomposed``.
    """
    group_id = canonical_definition_group_id(code, ("restriction:R105:C300",))
    fact_id = canonical_definition_fact_id(
        code, group_id, "restriction", "R105", "C300"
    )
    return Decomposition(
        code=code,
        semantic_type="Neoplastic Process",
        constituents=(),
        complete_definition=CompleteDefinition(
            root_code=code,
            facts=(
                RestrictionDefinitionFact(
                    fact_id=fact_id,
                    anchor_code=code,
                    group_id=group_id,
                    depth=0,
                    role_code="R105",
                    filler_code="C300",
                ),
            ),
            groups=(DefinitionGroup(group_id=group_id, anchor_code=code, depth=0),),
            root_group_ids=(group_id,),
        ),
    )


@pytest.mark.integration
@pytest.mark.mutating_integration
async def test_completion_metrics_match_for_shared_facts_and_residual_concepts() -> (
    None
):
    """The persisted definition metrics must be scoped exactly like the pipeline.

    Two independent ways this can drift, each of which makes ``finish_run`` reject
    every well-formed run of this shape and is therefore unrecoverable:

    * a run-wide ``count(DISTINCT ...)`` under-counts ``projected_fact_count`` when
      two roots share a defined genus;
    * counting definition rows for concepts that are not ``is_decomposed``
      over-counts, because a residual concept still persists its definition.
    """
    dsn = _asyncpg_dsn(get_settings().database_url)
    engine = make_engine(get_settings().database_url)
    sf = make_sessionmaker(engine)
    store = ProvenanceStore(sf)
    codes = ("C1", "C2", "C3")
    try:
        await _cleanup(dsn)
        await store.create_run(_SHARED_GENUS_RUN_ID, "26.07d", _fingerprint(codes))
        for code, decomposition in (
            ("C1", _shared_genus_decomposition("C1", 1)),
            ("C2", _shared_genus_decomposition("C2", 2)),
            ("C3", _residual_decomposition("C3")),
        ):
            claim = await store.claim_work_item(_SHARED_GENUS_RUN_ID, code)
            assert claim is not None
            await store.complete_work_item(
                _SHARED_GENUS_RUN_ID,
                code,
                claim,
                decomposition=decomposition,
                minted=(),
                semantic_types=("Neoplastic Process",),
            )

        decompositions = await store.decompositions_for_run(_SHARED_GENUS_RUN_ID)
        # The residual concept is excluded from the reconstruction ...
        assert [item.code for item in decompositions] == ["C1", "C2"]
        # ... but its definition fact IS persisted, so an unscoped count sees it.
        conn = await asyncpg.connect(dsn)
        try:
            persisted_facts = await conn.fetchval(
                "SELECT count(*) FROM decomp_definition_fact WHERE run_id = $1",
                _SHARED_GENUS_RUN_ID,
            )
            distinct_source_ids = await conn.fetchval(
                "SELECT count(DISTINCT source_id.value) FROM decomp_constituent c "
                "CROSS JOIN LATERAL "
                "jsonb_array_elements_text(c.source_definition_ids) "
                "AS source_id(value) WHERE c.run_id = $1",
                _SHARED_GENUS_RUN_ID,
            )
        finally:
            await conn.close()
        assert persisted_facts == 3
        # Run-wide DISTINCT collapses the shared fact; the per-concept sum is 2.
        assert distinct_source_ids == 1

        metrics = await _completion_metrics(store, _SHARED_GENUS_RUN_ID)
        assert metrics["complete_fact_count"] == 2
        assert metrics["projected_fact_count"] == 2
        assert metrics["complete_definition_count"] == 2

        assert (
            await store.finish_run(
                _SHARED_GENUS_RUN_ID,
                source_identity="a" * 64,
                metrics=metrics,
            )
            is True
        )
    finally:
        await _cleanup(dsn)
        await dispose_engine(engine)


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
            decomposition=_repeated_occurrence_decomposition(),
            minted=(),
            semantic_types=("Neoplastic Process",),
        )
        assert await store.pending_codes(_RUN_ID) == []
        persisted = await store.decompositions_for_run(_RUN_ID)
        assert persisted[0].constituents[0].axis == "op:PrimarySite"
        assert persisted[0].constituents[0].source_roles == ("R101",)
        complete_definition = persisted[0].complete_definition
        assert complete_definition is not None
        assert len(complete_definition.occurrences) == 2
        expected_occurrence_ids = tuple(
            sorted(
                canonical_source_occurrence_id(
                    "C6135",
                    complete_definition.occurrences[0].source_fact_id,
                    (0, position),
                )
                for position in (0, 1)
            )
        )
        assert (
            persisted[0].constituents[0].source_occurrence_ids
            == expected_occurrence_ids
        )

        finished = await store.finish_run(
            _RUN_ID,
            source_identity="a" * 64,
            metrics=await _completion_metrics(store, _RUN_ID),
        )
        assert finished is True
    finally:
        await _cleanup(dsn)
        await dispose_engine(engine)


@pytest.mark.integration
async def test_finish_run_requires_complete_metrics_matching_persisted_outcomes() -> (
    None
):
    dsn = _asyncpg_dsn(get_settings().database_url)
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    try:
        await _cleanup(dsn)
        await store.create_run(_RUN_ID, "26.07d", _fingerprint(()))

        with pytest.raises(ValidationError, match="Field required"):
            await store.finish_run(
                _RUN_ID,
                source_identity="a" * 64,
                metrics={},
            )

        mismatched = await _completion_metrics(store, _RUN_ID)
        mismatched |= {"total_in_scope": 1, "atomic_noop": 1}
        with pytest.raises(RunStateError, match="do not match persisted"):
            await store.finish_run(
                _RUN_ID,
                source_identity="a" * 64,
                metrics=mismatched,
            )

        await _cleanup(dsn)
        await store.create_run(_RUN_ID, "26.07d", _fingerprint(("C1",)))
        claim = await store.claim_work_item(_RUN_ID, "C1")
        assert claim is not None
        await store.complete_work_item(
            _RUN_ID,
            "C1",
            claim,
            decomposition=Decomposition(
                code="C1",
                semantic_type="Neoplastic Process",
                constituents=(
                    Constituent(
                        axis="R101",
                        filler_code="C2",
                        axis_source="role",
                    ),
                ),
            ),
            minted=(),
            semantic_types=("Neoplastic Process",),
        )
        mismatched = await _completion_metrics(store, _RUN_ID)
        mismatched["complete_definition_count"] = 1
        with pytest.raises(RunStateError, match="definition metrics"):
            await store.finish_run(
                _RUN_ID,
                source_identity="a" * 64,
                metrics=mismatched,
            )
    finally:
        await _cleanup(dsn)
        await dispose_engine(engine)


@pytest.mark.integration
async def test_non_decomposition_outcomes_round_trip_as_distinct_database_states() -> (
    None
):
    dsn = _asyncpg_dsn(get_settings().database_url)
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    try:
        await _cleanup(dsn)
        await store.create_run(
            _RUN_ID,
            "26.07d",
            _fingerprint(("C162770", "C999")),
        )
        await store.create_run(
            _RERUN_ID,
            "26.07d",
            _fingerprint(("C102883",)),
        )

        excluded_claim = await store.claim_work_item(_RUN_ID, "C162770")
        atomic_claim = await store.claim_work_item(_RERUN_ID, "C102883")
        assert excluded_claim is not None
        assert atomic_claim is not None

        await store.complete_work_item(
            _RUN_ID,
            "C162770",
            excluded_claim,
            decomposition=None,
            outcome="semantic-excluded",
            semantic_types=("Finding",),
            minted=(),
        )
        await store.complete_work_item(
            _RERUN_ID,
            "C102883",
            atomic_claim,
            decomposition=None,
            outcome="atomic-no-op",
            semantic_types=("Neoplastic Process",),
            minted=(),
        )

        conn = await asyncpg.connect(dsn)
        try:
            rows = await conn.fetch(
                "SELECT concept_code, outcome, semantic_type, "
                "semantic_types::text AS semantic_types "
                "FROM decomp_work_item WHERE run_id = ANY($1) "
                "AND state = 'complete' "
                "ORDER BY concept_code",
                [_RUN_ID, _RERUN_ID],
            )
        finally:
            await conn.close()

        assert [dict(row) for row in rows] == [
            {
                "concept_code": "C102883",
                "outcome": "atomic-no-op",
                "semantic_type": "Neoplastic Process",
                "semantic_types": '["Neoplastic Process"]',
            },
            {
                "concept_code": "C162770",
                "outcome": "semantic-excluded",
                "semantic_type": "Finding",
                "semantic_types": '["Finding"]',
            },
        ]
        excluded_counts = await store.outcome_counts(_RUN_ID)
        assert excluded_counts.total_in_scope == 2
        assert excluded_counts.semantic_excluded == 1
        assert excluded_counts.atomic_noop == 0
        atomic_counts = await store.outcome_counts(_RERUN_ID)
        assert atomic_counts.total_in_scope == 1
        assert atomic_counts.semantic_excluded == 0
        assert atomic_counts.atomic_noop == 1

        pending_claim = await store.claim_work_item(_RUN_ID, "C999")
        assert pending_claim is not None
        await store.fail_work_item(
            _RUN_ID,
            "C999",
            pending_claim,
            RuntimeError("transient failure"),
        )
        await store.resume_run(
            _RUN_ID,
            RunResumeIdentity.from_fingerprint(_fingerprint(("C162770", "C999"))),
        )
        resumed_outcomes = await store.work_item_outcomes(_RUN_ID)
        assert resumed_outcomes[0].model_dump() == {
            "run_id": _RUN_ID,
            "concept_code": "C162770",
            "ordinal": 0,
            "state": "complete",
            "outcome": "semantic-excluded",
            "semantic_type": "Finding",
            "semantic_types": ("Finding",),
            "is_decomposed": False,
            "is_residual": False,
            "constituent_count": 0,
            "minted_count": 0,
        }
        assert resumed_outcomes[1].state == "failed"
        assert resumed_outcomes[1].outcome is None

        retry_claim = await store.claim_work_item(_RUN_ID, "C999")
        assert retry_claim is not None
        await store.complete_work_item(
            _RUN_ID,
            "C999",
            retry_claim,
            decomposition=None,
            outcome="atomic-no-op",
            semantic_types=("Neoplastic Process",),
            minted=(),
        )
        resumed_counts = await store.outcome_counts(_RUN_ID)
        assert resumed_counts.total_in_scope == 2
        assert resumed_counts.semantic_excluded == 1
        assert resumed_counts.atomic_noop == 1
    finally:
        await _cleanup(dsn)
        await dispose_engine(engine)


@pytest.mark.integration
async def test_publication_state_is_retryable_separate_and_completion_gated(
    tmp_path: Path,
) -> None:
    dsn = _asyncpg_dsn(get_settings().database_url)
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    identity = "b" * 64
    artifact_path = str(tmp_path / "decomposed.ttl")
    built_at = datetime.datetime(2026, 7, 30, 12, 0, tzinfo=datetime.UTC)
    try:
        await _cleanup(dsn)
        await store.create_run(
            _PUBLICATION_RUN_ID,
            "26.07d",
            _publication_fingerprint(),
        )
        pending = await store.get_run(_PUBLICATION_RUN_ID)
        assert pending is not None
        assert pending.publication_state == "pending"

        with pytest.raises(RuntimeError, match="publication has not completed"):
            await store.finish_run(
                _PUBLICATION_RUN_ID,
                source_identity="a" * 64,
                metrics={},
                representation_identity=identity,
            )

        await store.begin_publication(
            _PUBLICATION_RUN_ID,
            representation_identity=identity,
            artifact_path=artifact_path,
            built_at=built_at,
            predecessor=None,
        )
        publishing = await store.get_run(_PUBLICATION_RUN_ID)
        assert publishing is not None
        assert publishing.status == "running"
        assert publishing.publication_state == "publishing"
        assert publishing.publication_attempt_count == 1
        assert publishing.representation_identity == identity

        with pytest.raises(
            RunIdentityMismatchError,
            match="completion representation identity",
        ):
            await store.finish_run(
                _PUBLICATION_RUN_ID,
                source_identity="a" * 64,
                metrics={},
                representation_identity="c" * 64,
            )

        await _assert_processing_and_publication_failures_remain_separate(store)

        with pytest.raises(
            RunIdentityMismatchError,
            match="does not match the persisted intent",
        ):
            await store.begin_publication(
                _PUBLICATION_RUN_ID,
                representation_identity="c" * 64,
                artifact_path=str(tmp_path / "different.ttl"),
                built_at=built_at,
                predecessor=None,
            )

        await store.begin_publication(
            _PUBLICATION_RUN_ID,
            representation_identity=identity,
            artifact_path=artifact_path,
            built_at=built_at,
            predecessor=None,
        )
        retried = await store.get_run(_PUBLICATION_RUN_ID)
        assert retried is not None
        assert retried.publication_state == "publishing"
        assert retried.publication_attempt_count == 2
        assert retried.publication_error_type is None
        assert retried.publication_error_message is None

        assert await store.finish_run(
            _PUBLICATION_RUN_ID,
            source_identity="a" * 64,
            metrics=await _completion_metrics(store, _PUBLICATION_RUN_ID),
            representation_identity=identity,
        )
        complete = await store.get_run(_PUBLICATION_RUN_ID)
        assert complete is not None
        assert complete.status == "complete"
        assert complete.publication_state == "published"
        assert complete.publication_finished_at is not None
        evidence_run = await store.completed_run_for_evidence(_PUBLICATION_RUN_ID)
        assert evidence_run.fingerprint == _publication_fingerprint()
        assert evidence_run.representation_identity == identity
        assert evidence_run.publication_artifact_path == artifact_path
    finally:
        await _cleanup(dsn)
        await dispose_engine(engine)


@pytest.mark.integration
async def test_current_evidence_generator_reads_real_published_postgres_run(
    tmp_path: Path,
) -> None:
    dsn = _asyncpg_dsn(get_settings().database_url)
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    manifest = load_sample_manifest(_CURRENT_MANIFEST)
    artifact = tmp_path / "decomposed.ttl"
    artifact.write_text(
        "<http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C6135> "
        "<https://w3id.org/ontoprism/vocab#representationStatus> "
        '"legacy-precoordinated" ; '
        "<https://w3id.org/ontoprism/vocab#decomposedBy> "
        f'"{_CURRENT_EVIDENCE_RUN_ID}" .\n'
    )
    representation_identity = hashlib.sha256(artifact.read_bytes()).hexdigest()
    fingerprint = RunFingerprint(
        schema_version=5,
        source_identity=manifest.source_identity,
        collapse_policy_identity="0" * 64,
        branch=manifest.branch,
        scope_root=manifest.scope_root,
        scope_version=manifest.scope_version,
        semantic_types=("Neoplastic Process",),
        worklist=manifest.codes,
        sample_manifest_identity=manifest.identity,
        algorithm_version="decomposition-v3",
        config_version="nested-definition-v2",
        walker_max_depth=5,
        output_mode="file",
        load_mode="named-graph",
        emitted_at=datetime.datetime(2026, 8, 15, 12, tzinfo=datetime.UTC),
    )
    try:
        await _cleanup(dsn)
        await store.create_run(_CURRENT_EVIDENCE_RUN_ID, "26.07d", fingerprint)
        for code in manifest.codes:
            claim = await store.claim_work_item(_CURRENT_EVIDENCE_RUN_ID, code)
            assert claim is not None
            await store.complete_work_item(
                _CURRENT_EVIDENCE_RUN_ID,
                code,
                claim,
                decomposition=(
                    _repeated_occurrence_decomposition() if code == "C6135" else None
                ),
                outcome=None if code == "C6135" else "atomic-no-op",
                semantic_types=("Neoplastic Process",),
                minted=(),
            )
        await store.begin_publication(
            _CURRENT_EVIDENCE_RUN_ID,
            representation_identity=representation_identity,
            artifact_path=str(artifact.resolve()),
            built_at=datetime.datetime.now(datetime.UTC),
            predecessor=None,
        )
        assert await store.finish_run(
            _CURRENT_EVIDENCE_RUN_ID,
            source_identity=manifest.source_identity,
            metrics=await _completion_metrics(store, _CURRENT_EVIDENCE_RUN_ID),
            representation_identity=representation_identity,
        )

        aggregate_query_count = 0

        def count_aggregate_queries(*_args: object) -> None:
            nonlocal aggregate_query_count
            aggregate_query_count += 1

        event.listen(
            engine.sync_engine, "before_cursor_execute", count_aggregate_queries
        )
        try:
            aggregate = await store.corpus_baseline_aggregate(_CURRENT_EVIDENCE_RUN_ID)
        finally:
            event.remove(
                engine.sync_engine, "before_cursor_execute", count_aggregate_queries
            )
        assert aggregate_query_count == 1
        assert aggregate.worklist_count == len(manifest.codes)
        assert aggregate.outcome_counts.decomposed == 1
        assert aggregate.outcome_counts.atomic_noop == len(manifest.codes) - 1
        assert aggregate.decomposed_codes == ("C6135",)
        assert aggregate.emitted_constituent_pair_count == 1
        assert aggregate.complete_semantic_fact_count == 1
        assert aggregate.source_occurrence_count == 2
        assert aggregate.selected_occurrence_count == 2
        assert aggregate.minted_count == 0

        evidence, comparison = await generate_current_evidence(
            sample_manifest=_CURRENT_MANIFEST,
            oracle=_CURRENT_GOLDEN / "neoplasm-adjudicated.json",
            row_decisions=_CURRENT_GOLDEN / "neoplasm-row-decisions.json",
            proposal_registry=_CURRENT_GOLDEN / "proposal-registry.json",
            run_id=_CURRENT_EVIDENCE_RUN_ID,
            artifact=artifact,
            engine_output=tmp_path / "engine.json",
            comparison_output=tmp_path / "comparison.json",
            store=store,
        )

        assert tuple(item.code for item in evidence.concepts) == manifest.codes
        concept = next(item for item in evidence.concepts if item.code == "C6135")
        assert len(concept.all_source_occurrences) == 2
        assert comparison.current_evidence_identity == evidence.evidence_identity
    finally:
        await _cleanup(dsn)
        await dispose_engine(engine)


@pytest.mark.integration
async def test_publication_intent_requires_an_existing_finished_worklist(
    tmp_path: Path,
) -> None:
    dsn = _asyncpg_dsn(get_settings().database_url)
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    fingerprint = _publication_fingerprint().model_copy(update={"worklist": ("C1",)})
    try:
        await _cleanup(dsn)
        await store.create_run(_PUBLICATION_RUN_ID, "26.07d", fingerprint)
        with pytest.raises(RunStateError, match="unfinished work items"):
            await store.begin_publication(
                _PUBLICATION_RUN_ID,
                representation_identity="b" * 64,
                artifact_path=str(tmp_path / "decomposed.ttl"),
                built_at=datetime.datetime.now(datetime.UTC),
                predecessor=None,
            )
        assert await store.fail_run(
            _PUBLICATION_RUN_ID,
            RuntimeError("processing stopped"),
        )
        with pytest.raises(RunStateError, match="not running"):
            await store.begin_publication(
                _PUBLICATION_RUN_ID,
                representation_identity="b" * 64,
                artifact_path=str(tmp_path / "decomposed.ttl"),
                built_at=datetime.datetime.now(datetime.UTC),
                predecessor=None,
            )
        with pytest.raises(RunStateError, match="no active publication"):
            await store.record_publication_failure(
                _PUBLICATION_RUN_ID,
                RuntimeError("not publishing"),
            )
        with pytest.raises(RunStateError, match="does not exist"):
            await store.begin_publication(
                "missing-publication-run",
                representation_identity="b" * 64,
                artifact_path=str(tmp_path / "decomposed.ttl"),
                built_at=datetime.datetime.now(datetime.UTC),
                predecessor=None,
            )

        await store.create_run(_RUN_ID, "26.07d", _fingerprint(()))
        with pytest.raises(RunStateError, match="publication is 'not_requested'"):
            await store.begin_publication(
                _RUN_ID,
                representation_identity="b" * 64,
                artifact_path=str(tmp_path / "decomposed.ttl"),
                built_at=datetime.datetime.now(datetime.UTC),
                predecessor=None,
            )
        with pytest.raises(
            RunIdentityMismatchError,
            match="non-publishing run cannot complete",
        ):
            await store.finish_run(
                _RUN_ID,
                source_identity="a" * 64,
                metrics={},
                representation_identity="b" * 64,
            )
    finally:
        await _cleanup(dsn)
        await dispose_engine(engine)


@pytest.mark.integration
async def test_publication_lock_holds_the_verified_postgres_advisory_key() -> None:
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    contender = await asyncpg.connect(_asyncpg_dsn(get_settings().database_url))
    key = "decomposition:publication"
    try:
        async with store.publication_lock():
            assert (
                await contender.fetchval(
                    "SELECT pg_try_advisory_lock(hashtextextended($1, 0))",
                    key,
                )
                is False
            )
        assert (
            await contender.fetchval(
                "SELECT pg_try_advisory_lock(hashtextextended($1, 0))",
                key,
            )
            is True
        )
        assert (
            await contender.fetchval(
                "SELECT pg_advisory_unlock(hashtextextended($1, 0))",
                key,
            )
            is True
        )
    finally:
        await contender.close()
        await dispose_engine(engine)


@pytest.mark.integration
async def test_cancelled_waiter_does_not_leak_the_publication_lock() -> None:
    engine = make_engine(get_settings().database_url)
    first = ProvenanceStore(make_sessionmaker(engine))
    second = ProvenanceStore(make_sessionmaker(engine))
    contender = await asyncpg.connect(_asyncpg_dsn(get_settings().database_url))
    key = "decomposition:publication"

    async def wait_for_lock() -> None:
        async with second.publication_lock():
            raise AssertionError("cancelled waiter entered the publication body")

    try:
        async with first.publication_lock():
            waiter = asyncio.create_task(wait_for_lock())
            await asyncio.sleep(0.05)
            waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter

        assert (
            await contender.fetchval(
                "SELECT pg_try_advisory_lock(hashtextextended($1, 0))",
                key,
            )
            is True
        )
        assert (
            await contender.fetchval(
                "SELECT pg_advisory_unlock(hashtextextended($1, 0))",
                key,
            )
            is True
        )
    finally:
        await contender.close()
        await dispose_engine(engine)


@pytest.mark.integration
async def test_failed_publication_unlock_invalidates_connection_and_releases_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    contender = await asyncpg.connect(_asyncpg_dsn(get_settings().database_url))
    key = "decomposition:publication"

    async def fail_unlock(_connection: object) -> None:
        raise RuntimeError("injected unlock failure")

    monkeypatch.setattr(
        provenance_module,
        "_release_publication_lock",
        fail_unlock,
    )
    try:
        with pytest.raises(RuntimeError, match="injected unlock failure"):
            async with store.publication_lock():
                pass

        assert (
            await contender.fetchval(
                "SELECT pg_try_advisory_lock(hashtextextended($1, 0))",
                key,
            )
            is True
        )
        assert (
            await contender.fetchval(
                "SELECT pg_advisory_unlock(hashtextextended($1, 0))",
                key,
            )
            is True
        )
    finally:
        await contender.close()
        await dispose_engine(engine)


@pytest.mark.integration
async def test_body_and_unlock_failure_preserve_body_error_and_release_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    contender = await asyncpg.connect(_asyncpg_dsn(get_settings().database_url))
    key = "decomposition:publication"
    body_error = ValueError("publication body failed")

    async def fail_unlock(_connection: object) -> None:
        raise RuntimeError("injected unlock failure")

    monkeypatch.setattr(
        provenance_module,
        "_release_publication_lock",
        fail_unlock,
    )
    try:
        with pytest.raises(ValueError, match="publication body failed") as exc_info:
            async with store.publication_lock():
                raise body_error

        assert exc_info.value is body_error
        assert any("injected unlock failure" in note for note in body_error.__notes__)
        assert (
            await contender.fetchval(
                "SELECT pg_try_advisory_lock(hashtextextended($1, 0))",
                key,
            )
            is True
        )
        assert (
            await contender.fetchval(
                "SELECT pg_advisory_unlock(hashtextextended($1, 0))",
                key,
            )
            is True
        )
    finally:
        await contender.close()
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
            semantic_types=("Neoplastic Process",),
        )
        assert await store.finish_run(
            _RUN_ID,
            source_identity="a" * 64,
            metrics=await _completion_metrics(store, _RUN_ID),
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
            semantic_types=("Neoplastic Process",),
        )
        assert await store.finish_run(
            _RERUN_ID,
            source_identity="a" * 64,
            metrics=await _completion_metrics(store, _RERUN_ID),
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
