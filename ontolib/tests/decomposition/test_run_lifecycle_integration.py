"""Real-Postgres contracts for exact, source-bound decomposition runs."""

from __future__ import annotations

import asyncio
import datetime
import json
from typing import TYPE_CHECKING

import asyncpg
import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.decomposition import run as run_module
from ontolib.decomposition.minting import MintedConcept
from ontolib.decomposition.models import (
    CompleteDefinition,
    Constituent,
    Decomposition,
    DefinitionGroup,
    GenusDefinitionFact,
    RestrictionDefinitionFact,
    canonical_definition_fact_id,
    canonical_definition_group_id,
)
from ontolib.decomposition.provenance import (
    ProvenanceStore,
    RunIdentityMismatchError,
    RunStateError,
)
from ontolib.decomposition.provenance_models import (
    NcitSourceSnapshot,
    RunFingerprint,
    RunResumeIdentity,
)
from ontolib.decomposition.run import RunConfig, _new_run_id, run_pipeline

if TYPE_CHECKING:
    from collections.abc import Collection
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = [
    pytest.mark.integration,
    pytest.mark.mutating_integration,
    pytest.mark.usefixtures("isolated_postgres_settings"),
]


def _dsn() -> str:
    return get_settings().database_url.replace("+asyncpg", "")


def _fingerprint(*, source: str = "a" * 64) -> RunFingerprint:
    return RunFingerprint(
        source_identity=source,
        branch="neoplasm",
        scope_root="C3262",
        scope_version="stated-genus-subclass-v1",
        semantic_types=("Disease or Syndrome", "Neoplastic Process"),
        worklist=("C0", "C1"),
        total_limit=2,
        algorithm_version="decomposition-v1",
        config_version="axes-v1",
        walker_max_depth=5,
        output_mode="none",
        load_mode="none",
        emitted_at=datetime.datetime(2026, 7, 29, 12, 0, tzinfo=datetime.UTC),
    )


async def _cleanup(run_ids: list[str]) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
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


class _LifecycleClient:
    async def version(self) -> str:
        return "26.07d"

    async def select(
        self,
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> list[dict[str, str]]:
        del required_variables
        raise AssertionError(f"unexpected query: {query}")

    async def select_once(
        self,
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> list[dict[str, str]]:
        return await self.select(query, required_variables=required_variables)


class _RecordingStore(ProvenanceStore):
    def __init__(self, sf: async_sessionmaker[AsyncSession]) -> None:
        super().__init__(sf)
        self.created: list[str] = []

    async def create_run(
        self,
        run_id: str,
        ncit_version: str,
        fingerprint: RunFingerprint,
    ) -> None:
        self.created.append(run_id)
        await super().create_run(run_id, ncit_version, fingerprint)


class _InterruptedDecomposer:
    def __init__(self) -> None:
        self._c2_failed = False

    async def __call__(
        self,
        code: str,
        *_args: object,
        **_kwargs: object,
    ) -> object:
        if code == "C2" and not self._c2_failed:
            self._c2_failed = True
            raise RuntimeError("injected interruption")
        return run_module._CandidateResult(
            decomposition=Decomposition(
                code=code,
                semantic_type="Neoplastic Process",
                constituents=[
                    Constituent(
                        axis="R88",
                        filler_code=f"F-{code}",
                        axis_source="role",
                    )
                ],
            ),
            outcome="decomposed",
            semantic_types=("Neoplastic Process",),
        )


async def _two_codes(*_args: object, **_kwargs: object) -> list[str]:
    return ["C1", "C2"]


async def _no_residuals(*_args: object, **_kwargs: object) -> set[str]:
    return set()


async def _source() -> NcitSourceSnapshot:
    return NcitSourceSnapshot(
        source_identity="a" * 64,
        ontology_version="26.07d",
    )


@pytest.mark.parametrize("count", [2])
async def test_concurrent_starts_materialize_distinct_exact_worklists(
    count: int,
) -> None:
    run_ids = [_new_run_id("neoplasm") for _ in range(count)]
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    try:
        await asyncio.gather(
            *(store.create_run(run_id, "26.07d", _fingerprint()) for run_id in run_ids)
        )

        assert len(set(run_ids)) == count
        assert await store.pending_codes(run_ids[0]) == ["C0", "C1"]
        assert await store.pending_codes(run_ids[1]) == ["C0", "C1"]
    finally:
        await _cleanup(run_ids)
        await dispose_engine(engine)


async def test_zero_output_and_decomposition_complete_as_exact_work_items() -> None:
    run_id = _new_run_id("neoplasm")
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    try:
        await store.create_run(run_id, "26.07d", _fingerprint())

        zero_claim = await store.claim_work_item(run_id, "C0")
        assert zero_claim is not None
        await store.complete_work_item(
            run_id,
            "C0",
            zero_claim,
            decomposition=None,
            minted=(),
            outcome="atomic-no-op",
            semantic_types=("Neoplastic Process",),
        )
        assert await store.pending_codes(run_id) == ["C1"]

        restriction_group_id = canonical_definition_group_id(
            "C1", ("restriction:R101:C12400",)
        )
        restriction_id = canonical_definition_fact_id(
            "C1",
            restriction_group_id,
            "restriction",
            "R101",
            "C12400",
        )
        root_group_id = canonical_definition_group_id(
            "C1",
            ("genus:C2916:defined", f"group:{restriction_group_id}"),
        )
        genus_id = canonical_definition_fact_id(
            "C1", root_group_id, "genus", "C2916", "defined"
        )
        decomposition = Decomposition(
            code="C1",
            semantic_type="Neoplastic Process",
            constituents=[
                Constituent(
                    axis="R101",
                    filler_code="C12400",
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
                        fact_id=genus_id,
                        anchor_code="C1",
                        group_id=root_group_id,
                        depth=0,
                        genus_code="C2916",
                        is_defined=True,
                    ),
                    RestrictionDefinitionFact(
                        fact_id=restriction_id,
                        anchor_code="C1",
                        group_id=restriction_group_id,
                        depth=0,
                        role_code="R101",
                        filler_code="C12400",
                    ),
                ),
                groups=(
                    DefinitionGroup(
                        group_id=root_group_id,
                        anchor_code="C1",
                        depth=0,
                        child_group_ids=(restriction_group_id,),
                    ),
                    DefinitionGroup(
                        group_id=restriction_group_id,
                        anchor_code="C1",
                        depth=0,
                    ),
                ),
                root_group_ids=(root_group_id,),
            ),
        )
        mint = MintedConcept(axis="op:Laterality", label="Left")
        claim = await store.claim_work_item(run_id, "C1")
        assert claim is not None
        await store.complete_work_item(
            run_id,
            "C1",
            claim,
            decomposition=decomposition,
            minted=(mint,),
            semantic_types=("Neoplastic Process",),
        )

        assert await store.pending_codes(run_id) == []
        assert await store.decompositions_for_run(run_id) == [decomposition]
        counts = await store.outcome_counts(run_id)
        assert counts.total_in_scope == 2
        assert counts.decomposed == 1
        assert counts.residual == 0
        assert counts.minted_count == 1
        assert await store.finish_run(
            run_id,
            source_identity="a" * 64,
            metrics=await _completion_metrics(store, run_id),
        )

        with pytest.raises(RunStateError, match="complete"):
            await store.resume_run(
                run_id,
                RunResumeIdentity.from_fingerprint(_fingerprint()),
            )
    finally:
        await _cleanup([run_id])
        await dispose_engine(engine)


async def test_empty_complete_definition_survives_postgres_round_trip() -> None:
    run_id = _new_run_id("neoplasm")
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    decomposition = Decomposition(
        code="C0",
        semantic_type="Neoplastic Process",
        constituents=(
            Constituent(
                axis="op:Laterality",
                filler_code="C25229",
                axis_source="nlp",
            ),
        ),
        complete_definition=CompleteDefinition(root_code="C0", facts=()),
    )
    try:
        await store.create_run(run_id, "26.07d", _fingerprint())
        claim = await store.claim_work_item(run_id, "C0")
        assert claim is not None
        await store.complete_work_item(
            run_id,
            "C0",
            claim,
            decomposition=decomposition,
            minted=(),
            semantic_types=("Neoplastic Process",),
        )

        assert await store.decompositions_for_run(run_id) == [decomposition]
    finally:
        await _cleanup([run_id])
        await dispose_engine(engine)


@pytest.mark.parametrize(
    ("child_table", "mismatch"),
    [
        ("decomp_constituent", "missing"),
        ("decomp_minted_proposal", "missing"),
        ("decomp_constituent", "extra"),
        ("decomp_minted_proposal", "extra"),
    ],
)
async def test_persisted_completion_counts_gate_reconstruction_and_finalization(
    child_table: str,
    mismatch: str,
) -> None:
    run_id = _new_run_id("neoplasm")
    fingerprint = _fingerprint().model_copy(
        update={"worklist": ("C0",), "total_limit": 1}
    )
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    conn = await asyncpg.connect(_dsn())
    try:
        await store.create_run(run_id, "26.07d", fingerprint)
        claim = await store.claim_work_item(run_id, "C0")
        assert claim is not None
        await store.complete_work_item(
            run_id,
            "C0",
            claim,
            decomposition=Decomposition(
                code="C0",
                semantic_type="Neoplastic Process",
                constituents=(
                    Constituent(
                        axis="op:Laterality",
                        filler_code="MINT-C0",
                        axis_source="nlp",
                    ),
                ),
            ),
            minted=(MintedConcept(axis="op:Laterality", label="minted C0"),),
            semantic_types=("Neoplastic Process",),
        )
        if mismatch == "missing":
            await conn.execute(
                f"DELETE FROM {child_table} "  # noqa: S608 - fixed parametrization
                "WHERE run_id = $1 AND concept_code = 'C0'",
                run_id,
            )
        elif child_table == "decomp_constituent":
            await conn.execute(
                "INSERT INTO decomp_constituent "
                "(run_id, concept_code, axis, filler_code, axis_source, source_role, "
                "most_specific, needs_review, relationship_group, "
                "source_definition_ids) SELECT run_id, concept_code, 'op:Extra', "
                "'C999999', axis_source, source_role, most_specific, needs_review, "
                "relationship_group, source_definition_ids FROM decomp_constituent "
                "WHERE run_id = $1 AND concept_code = 'C0' LIMIT 1",
                run_id,
            )
        else:
            await conn.execute(
                "INSERT INTO decomp_minted_proposal "
                "(run_id, concept_code, proposal_id, axis, label, source_signal, "
                "status) "
                "SELECT run_id, concept_code, 'MINT-extra', axis, 'extra', "
                "source_signal, status FROM decomp_minted_proposal "
                "WHERE run_id = $1 AND concept_code = 'C0' LIMIT 1",
                run_id,
            )

        with pytest.raises(RunStateError, match="persisted completion counts"):
            await store.decompositions_for_run(run_id)
        with pytest.raises(RunStateError, match="persisted completion counts"):
            await store.finish_run(
                run_id,
                source_identity=fingerprint.source_identity,
                metrics={},
            )
    finally:
        await conn.close()
        await _cleanup([run_id])
        await dispose_engine(engine)


async def test_finish_run_reconciles_lost_commit_acknowledgement() -> None:
    run_id = _new_run_id("neoplasm")
    fingerprint = _fingerprint().model_copy(
        update={"worklist": (), "total_limit": None}
    )
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    acknowledgement_lost = False

    def lose_first_commit_ack(session: Session) -> None:
        nonlocal acknowledgement_lost
        if session.bind is engine.sync_engine and not acknowledgement_lost:
            acknowledgement_lost = True
            raise RuntimeError("connection closed after commit")

    try:
        await store.create_run(run_id, "26.07d", fingerprint)
        event.listen(Session, "after_commit", lose_first_commit_ack)
        try:
            assert await store.finish_run(
                run_id,
                source_identity=fingerprint.source_identity,
                metrics=await _completion_metrics(store, run_id),
            )
        finally:
            event.remove(Session, "after_commit", lose_first_commit_ack)

        assert acknowledgement_lost is True
        completed = await store.get_run(run_id)
        assert completed is not None
        assert completed.status == "complete"
        assert completed.total_in_scope == 0
    finally:
        await _cleanup([run_id])
        await dispose_engine(engine)


async def test_failed_atomic_replace_rolls_back_then_retries_without_stale_rows() -> (
    None
):
    run_id = _new_run_id("neoplasm")
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    duplicate = Constituent(axis="R88", filler_code="C27970", axis_source="role")
    try:
        await store.create_run(run_id, "26.07d", _fingerprint())
        claim = await store.claim_work_item(run_id, "C1")
        assert claim is not None
        with pytest.raises(IntegrityError):
            await store.complete_work_item(
                run_id,
                "C1",
                claim,
                decomposition=Decomposition(
                    code="C1",
                    semantic_type="Neoplastic Process",
                    constituents=[duplicate, duplicate],
                ),
                minted=(),
                semantic_types=("Neoplastic Process",),
            )
        await store.fail_work_item(
            run_id,
            "C1",
            claim,
            RuntimeError("x" * 2000),
        )

        conn = await asyncpg.connect(_dsn())
        try:
            row = await conn.fetchrow(
                "SELECT state, error_type, error_message FROM decomp_work_item "
                "WHERE run_id = $1 AND concept_code = 'C1'",
                run_id,
            )
            constituent_count = await conn.fetchval(
                "SELECT count(*) FROM decomp_constituent "
                "WHERE run_id = $1 AND concept_code = 'C1'",
                run_id,
            )
        finally:
            await conn.close()
        assert dict(row) == {
            "state": "failed",
            "error_type": "RuntimeError",
            "error_message": "x" * 1000,
        }
        assert constituent_count == 0

        resumed = await store.resume_run(
            run_id, RunResumeIdentity.from_fingerprint(_fingerprint())
        )
        assert resumed == _fingerprint()
        retry_claim = await store.claim_work_item(run_id, "C1")
        assert retry_claim is not None
        assert retry_claim != claim
        replacement = Decomposition(
            code="C1",
            semantic_type="Neoplastic Process",
            constituents=[duplicate],
        )
        await store.complete_work_item(
            run_id,
            "C1",
            retry_claim,
            decomposition=replacement,
            minted=(),
            semantic_types=("Neoplastic Process",),
        )
        assert await store.decompositions_for_run(run_id) == [replacement]

        with pytest.raises(RunIdentityMismatchError, match="source"):
            await store.resume_run(
                run_id,
                RunResumeIdentity.from_fingerprint(_fingerprint(source="b" * 64)),
            )
    finally:
        await _cleanup([run_id])
        await dispose_engine(engine)


async def test_database_rejects_invalid_states_and_identity_mutation() -> None:
    run_id = _new_run_id("neoplasm")
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    try:
        await store.create_run(run_id, "26.07d", _fingerprint())
        conn = await asyncpg.connect(_dsn())
        try:
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    "UPDATE decomp_run SET status = 'looks-complete' WHERE id = $1",
                    run_id,
                )
            with pytest.raises(asyncpg.RaiseError, match="identity is immutable"):
                await conn.execute(
                    "UPDATE decomp_run SET source_identity = $2 WHERE id = $1",
                    run_id,
                    "b" * 64,
                )
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    "UPDATE decomp_work_item SET state = 'done' "
                    "WHERE run_id = $1 AND concept_code = 'C0'",
                    run_id,
                )
        finally:
            await conn.close()
    finally:
        await _cleanup([run_id])
        await dispose_engine(engine)


async def test_resume_rejects_materialized_worklist_tampering() -> None:
    run_id = _new_run_id("neoplasm")
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    try:
        await store.create_run(run_id, "26.07d", _fingerprint())
        conn = await asyncpg.connect(_dsn())
        try:
            await conn.execute(
                "DELETE FROM decomp_work_item "
                "WHERE run_id = $1 AND concept_code = 'C0'",
                run_id,
            )
        finally:
            await conn.close()

        with pytest.raises(RunIdentityMismatchError, match="worklist"):
            await store.resume_run(
                run_id,
                RunResumeIdentity.from_fingerprint(_fingerprint()),
            )
    finally:
        await _cleanup([run_id])
        await dispose_engine(engine)


async def test_invalid_completion_inputs_and_stale_claims_fail_closed() -> None:
    run_id = _new_run_id("neoplasm")
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    claim = None
    try:
        await store.create_run(run_id, "26.07d", _fingerprint())
        claim = await store.claim_work_item(run_id, "C0")
        assert claim is not None
        wrong_code = Decomposition(
            code="C1",
            semantic_type="Neoplastic Process",
        )
        with pytest.raises(ValueError, match="code"):
            await store.complete_work_item(
                run_id,
                "C0",
                claim,
                decomposition=wrong_code,
                minted=(),
                semantic_types=("Neoplastic Process",),
            )
        with pytest.raises(ValueError, match="require a decomposition"):
            await store.complete_work_item(
                run_id,
                "C0",
                claim,
                decomposition=None,
                minted=(MintedConcept(axis="op:Laterality", label="Left"),),
                outcome="atomic-no-op",
                semantic_types=("Neoplastic Process",),
            )
        stale_claim = claim.__class__(int=claim.int + 1)
        with pytest.raises(RunStateError, match="not owned"):
            await store.complete_work_item(
                run_id,
                "C0",
                stale_claim,
                decomposition=None,
                minted=(),
                outcome="atomic-no-op",
                semantic_types=("Neoplastic Process",),
            )
        with pytest.raises(RunStateError, match="claim changed"):
            await store.fail_work_item(
                run_id,
                "C0",
                stale_claim,
                RuntimeError("stale worker"),
            )
    finally:
        if claim is not None:
            await store.fail_work_item(
                run_id,
                "C0",
                claim,
                RuntimeError("test cleanup"),
            )
        await _cleanup([run_id])
        await dispose_engine(engine)


async def test_completion_rowcount_guard_matches_real_asyncpg_behavior() -> None:
    """A database-side suppressed UPDATE must surface as a lost claim.

    This is the real-driver counterpart to the focused session double: PostgreSQL
    reports zero affected rows when a BEFORE UPDATE trigger returns NULL.
    """
    run_id = _new_run_id("neoplasm")
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    conn = await asyncpg.connect(_dsn())
    claim = None
    trigger_installed = False
    try:
        await store.create_run(run_id, "26.07d", _fingerprint())
        claim = await store.claim_work_item(run_id, "C0")
        assert claim is not None
        await conn.execute(
            """
            CREATE FUNCTION suppress_decomp_completion() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.state = 'complete' THEN
                    RETURN NULL;
                END IF;
                RETURN NEW;
            END
            $$
            """
        )
        await conn.execute(
            """
            CREATE TRIGGER suppress_decomp_completion
            BEFORE UPDATE ON decomp_work_item
            FOR EACH ROW EXECUTE FUNCTION suppress_decomp_completion()
            """
        )
        trigger_installed = True

        with pytest.raises(RunStateError, match="claim changed before completion"):
            await store.complete_work_item(
                run_id,
                "C0",
                claim,
                decomposition=None,
                minted=(),
                outcome="atomic-no-op",
                semantic_types=("Neoplastic Process",),
            )

        persisted = await conn.fetchrow(
            "SELECT state, claim_token FROM decomp_work_item "
            "WHERE run_id = $1 AND concept_code = 'C0'",
            run_id,
        )
        assert persisted is not None
        assert persisted["state"] == "running"
        assert persisted["claim_token"] == claim
    finally:
        if trigger_installed:
            await conn.execute(
                "DROP TRIGGER suppress_decomp_completion ON decomp_work_item"
            )
            await conn.execute("DROP FUNCTION suppress_decomp_completion()")
        if claim is not None:
            await store.fail_work_item(
                run_id,
                "C0",
                claim,
                RuntimeError("test cleanup"),
            )
        await conn.close()
        await _cleanup([run_id])
        await dispose_engine(engine)


async def test_finish_and_resume_reject_invalid_run_identity_or_state() -> None:
    run_id = _new_run_id("neoplasm")
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    try:
        await store.create_run(run_id, "26.07d", _fingerprint())
        with pytest.raises(RunIdentityMismatchError, match="completion source"):
            await store.finish_run(
                run_id,
                source_identity="b" * 64,
                metrics={},
            )
        with pytest.raises(RunStateError, match="unfinished"):
            await store.finish_run(
                run_id,
                source_identity="a" * 64,
                metrics={},
            )
        assert await store.fail_run(run_id, RuntimeError("stop")) is True
        assert await store.invalidate_run(run_id, RuntimeError("too late")) is False
        with pytest.raises(RunStateError, match="does not exist"):
            await store.resume_run(
                "missing-run",
                RunResumeIdentity.from_fingerprint(_fingerprint()),
            )
    finally:
        await _cleanup([run_id])
        await dispose_engine(engine)


async def test_source_swap_invalidation_removes_every_partial_snapshot() -> None:
    run_id = _new_run_id("neoplasm")
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    try:
        await store.create_run(run_id, "26.07d", _fingerprint())
        for code in ("C0", "C1"):
            claim = await store.claim_work_item(run_id, code)
            assert claim is not None
            await store.complete_work_item(
                run_id,
                code,
                claim,
                decomposition=Decomposition(
                    code=code,
                    semantic_type="Neoplastic Process",
                    constituents=[
                        Constituent(
                            axis="R88",
                            filler_code=f"F-{code}",
                            axis_source="role",
                        )
                    ],
                ),
                minted=(),
                semantic_types=("Neoplastic Process",),
            )

        invalidated = await store.invalidate_run(
            run_id,
            RuntimeError("source identity changed"),
        )

        assert invalidated is True
        assert await store.decompositions_for_run(run_id) == []
        conn = await asyncpg.connect(_dsn())
        try:
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM decomp_definition_fact WHERE run_id = $1",
                    run_id,
                )
                == 0
            )
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM decomp_definition_group_edge "
                    "WHERE run_id = $1",
                    run_id,
                )
                == 0
            )
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM decomp_definition_group WHERE run_id = $1",
                    run_id,
                )
                == 0
            )
        finally:
            await conn.close()
        assert await store.pending_codes(run_id) == ["C0", "C1"]
        await store.resume_run(
            run_id,
            RunResumeIdentity.from_fingerprint(_fingerprint()),
        )
        assert await store.pending_codes(run_id) == ["C0", "C1"]
    finally:
        await _cleanup([run_id])
        await dispose_engine(engine)


async def test_mint_proposals_reach_the_curator_queue_only_on_completion() -> None:
    """D48: a proposal must not enter the global queue before the run completes.

    Promoting on per-item completion would let an interrupted or invalidated run
    permanently pollute the curator queue, which no other test would notice.
    """
    run_id = _new_run_id("neoplasm")
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    conn = await asyncpg.connect(_dsn())
    try:
        await store.create_run(run_id, "26.07d", _fingerprint())
        for code in ("C0", "C1"):
            claim = await store.claim_work_item(run_id, code)
            assert claim is not None
            await store.complete_work_item(
                run_id,
                code,
                claim,
                decomposition=Decomposition(
                    code=code,
                    semantic_type="Neoplastic Process",
                    constituents=[
                        Constituent(
                            axis="op:Laterality",
                            filler_code=f"MINT-{code}",
                            axis_source="nlp",
                        )
                    ],
                ),
                minted=(
                    MintedConcept(
                        axis="op:Laterality",
                        label=f"minted {code}",
                    ),
                ),
                semantic_types=("Neoplastic Process",),
            )

        queued = await conn.fetchval(
            "SELECT count(*) FROM minted_concept WHERE run_id = $1", run_id
        )
        proposed = await conn.fetchval(
            "SELECT count(*) FROM decomp_minted_proposal WHERE run_id = $1", run_id
        )
        assert queued == 0
        assert proposed == 2

        await store.finish_run(
            run_id,
            source_identity="a" * 64,
            metrics=await _completion_metrics(store, run_id),
        )

        assert (
            await conn.fetchval(
                "SELECT count(*) FROM minted_concept WHERE run_id = $1", run_id
            )
            == 2
        )
    finally:
        await conn.close()
        await _cleanup([run_id])
        await dispose_engine(engine)


async def test_invalidated_run_cannot_promote_its_partial_mint_proposals() -> None:
    """Proposals computed against a superseded source must never be promotable."""
    run_id = _new_run_id("neoplasm")
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    conn = await asyncpg.connect(_dsn())
    try:
        await store.create_run(run_id, "26.07d", _fingerprint())
        claim = await store.claim_work_item(run_id, "C0")
        assert claim is not None
        await store.complete_work_item(
            run_id,
            "C0",
            claim,
            decomposition=Decomposition(
                code="C0",
                semantic_type="Neoplastic Process",
                constituents=[
                    Constituent(
                        axis="op:Laterality",
                        filler_code="MINT-C0",
                        axis_source="nlp",
                    )
                ],
            ),
            minted=(MintedConcept(axis="op:Laterality", label="minted C0"),),
            semantic_types=("Neoplastic Process",),
        )

        invalidated = await store.invalidate_run(run_id, RuntimeError("source changed"))
        assert invalidated is True

        assert (
            await conn.fetchval(
                "SELECT count(*) FROM decomp_minted_proposal WHERE run_id = $1", run_id
            )
            == 0
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM minted_concept WHERE run_id = $1", run_id
            )
            == 0
        )

        # Re-running the discarded work without mints must not resurrect them.
        await store.resume_run(
            run_id,
            RunResumeIdentity.from_fingerprint(_fingerprint()),
        )
        for code in ("C0", "C1"):
            retry = await store.claim_work_item(run_id, code)
            assert retry is not None
            await store.complete_work_item(
                run_id,
                code,
                retry,
                decomposition=None,
                minted=(),
                outcome="atomic-no-op",
                semantic_types=("Neoplastic Process",),
            )
        await store.finish_run(
            run_id,
            source_identity="a" * 64,
            metrics=await _completion_metrics(store, run_id),
        )

        assert (
            await conn.fetchval(
                "SELECT count(*) FROM minted_concept WHERE run_id = $1", run_id
            )
            == 0
        )
    finally:
        await conn.close()
        await _cleanup([run_id])
        await dispose_engine(engine)


async def test_resume_recovers_a_work_item_abandoned_in_running() -> None:
    """A killed worker leaves a claim behind; resume must reclaim it, not deadlock.

    Without the ``running`` -> ``failed`` reset, ``pending_codes`` keeps returning the
    item while ``claim_work_item`` refuses it forever and the run is unresumable.
    """
    run_id = _new_run_id("neoplasm")
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    conn = await asyncpg.connect(_dsn())
    try:
        await store.create_run(run_id, "26.07d", _fingerprint())
        abandoned = await store.claim_work_item(run_id, "C0")
        assert abandoned is not None
        # Simulate SIGKILL: the claim is never completed and never failed.
        assert await store.claim_work_item(run_id, "C0") is None

        await store.fail_run(run_id, RuntimeError("worker died"))
        await store.resume_run(
            run_id,
            RunResumeIdentity.from_fingerprint(_fingerprint()),
        )

        row = await conn.fetchrow(
            "SELECT state, error_type, claim_token FROM decomp_work_item "
            "WHERE run_id = $1 AND concept_code = 'C0'",
            run_id,
        )
        assert row is not None
        assert row["state"] == "failed"
        assert row["error_type"] == "InterruptedRun"
        assert row["claim_token"] is None

        reclaimed = await store.claim_work_item(run_id, "C0")
        assert reclaimed is not None
        assert reclaimed != abandoned
    finally:
        await conn.close()
        await _cleanup([run_id])
        await dispose_engine(engine)


async def test_non_running_run_rejects_claims_and_completions() -> None:
    """A failed run must not accept writes from a worker still holding a claim."""
    run_id = _new_run_id("neoplasm")
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    conn = await asyncpg.connect(_dsn())
    try:
        await store.create_run(run_id, "26.07d", _fingerprint())
        claim = await store.claim_work_item(run_id, "C0")
        assert claim is not None

        await store.fail_run(run_id, RuntimeError("operator stopped the run"))

        assert await store.claim_work_item(run_id, "C1") is None
        with pytest.raises(RunStateError):
            await store.complete_work_item(
                run_id,
                "C0",
                claim,
                decomposition=None,
                minted=(),
                outcome="atomic-no-op",
                semantic_types=("Neoplastic Process",),
            )

        assert (
            await conn.fetchval(
                "SELECT count(*) FROM decomp_constituent WHERE run_id = $1", run_id
            )
            == 0
        )
    finally:
        await conn.close()
        await _cleanup([run_id])
        await dispose_engine(engine)


async def test_failed_run_cannot_be_finished_or_promote_its_proposals() -> None:
    """`fail_run` leaves proposals in place, so `finish_run` must refuse the run.

    Unlike `invalidate_run`, failing a run does not delete `decomp_minted_proposal`
    rows. A worker racing an operator stop would otherwise promote them into the
    global curator queue permanently while the run still reports as failed.
    """
    run_id = _new_run_id("neoplasm")
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    conn = await asyncpg.connect(_dsn())
    try:
        await store.create_run(run_id, "26.07d", _fingerprint())
        for code in ("C0", "C1"):
            claim = await store.claim_work_item(run_id, code)
            assert claim is not None
            await store.complete_work_item(
                run_id,
                code,
                claim,
                decomposition=Decomposition(
                    code=code,
                    semantic_type="Neoplastic Process",
                    constituents=[
                        Constituent(
                            axis="op:Laterality",
                            filler_code=f"MINT-{code}",
                            axis_source="nlp",
                        )
                    ],
                ),
                minted=(MintedConcept(axis="op:Laterality", label=f"minted {code}"),),
                semantic_types=("Neoplastic Process",),
            )

        await store.fail_run(run_id, RuntimeError("operator stopped the run"))

        with pytest.raises(RunStateError, match="not running"):
            await store.finish_run(run_id, source_identity="a" * 64, metrics={})

        assert (
            await conn.fetchval(
                "SELECT count(*) FROM minted_concept WHERE run_id = $1", run_id
            )
            == 0
        )
    finally:
        await conn.close()
        await _cleanup([run_id])
        await dispose_engine(engine)


async def test_fingerprint_that_does_not_hash_to_its_identity_is_rejected() -> None:
    """The SHA-256 binding is what ties the fingerprint blob to the run's identity.

    `RunResumeIdentity` omits `worklist`, and the worklist check compares against the
    *persisted* fingerprint, so without this binding a tampered fingerprint plus
    matching tampered work items would resume and finish cleanly.
    """
    run_id = _new_run_id("neoplasm")
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    conn = await asyncpg.connect(_dsn())
    fingerprint = _fingerprint()
    try:
        await conn.execute(
            "INSERT INTO decomp_run (id, branch, status, ncit_version, started_at, "
            "source_identity, fingerprint, fingerprint_sha256, emitted_at, "
            "error_type, error_message, publication_state) VALUES "
            "($1, 'neoplasm', 'failed', '26.07d', now(), $2, $3::jsonb, "
            "repeat('0', 64), $4, 'Boom', 'injected', 'not_requested')",
            run_id,
            fingerprint.source_identity,
            fingerprint.model_dump_json(),
            fingerprint.emitted_at,
        )

        with pytest.raises(RunIdentityMismatchError, match="SHA-256"):
            await store.resume_run(
                run_id,
                RunResumeIdentity.from_fingerprint(fingerprint),
            )
        with pytest.raises(RunIdentityMismatchError, match="SHA-256"):
            await store.finish_run(
                run_id,
                source_identity=fingerprint.source_identity,
                metrics={},
            )
    finally:
        await conn.close()
        await _cleanup([run_id])
        await dispose_engine(engine)


async def test_permuted_worklist_order_is_rejected_on_resume() -> None:
    """Order is part of the worklist identity: it fixes the processing sequence.

    A membership-only check would let permuted ordinals through, changing processing
    order and the emitted TTL while still claiming fresh/resumed equivalence.
    """
    run_id = _new_run_id("neoplasm")
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    conn = await asyncpg.connect(_dsn())
    try:
        await store.create_run(run_id, "26.07d", _fingerprint())
        await conn.execute(
            "UPDATE decomp_work_item SET ordinal = 3 - ordinal WHERE run_id = $1",
            run_id,
        )

        with pytest.raises(RunIdentityMismatchError, match="worklist"):
            await store.resume_run(
                run_id,
                RunResumeIdentity.from_fingerprint(_fingerprint()),
            )
    finally:
        await conn.close()
        await _cleanup([run_id])
        await dispose_engine(engine)


async def test_fail_run_reports_whether_the_failure_is_recorded() -> None:
    """`True` means "the run is recorded as failed", not "this call wrote it".

    `fail_work_item` already demotes the enclosing run, so an ordinary work-item
    failure reaches `fail_run` with the run already failed; reporting `False` there
    made the pipeline claim an unrecorded failure on every routine error.
    """
    running_id = _new_run_id("neoplasm")
    already_failed_id = _new_run_id("neoplasm")
    complete_id = _new_run_id("neoplasm")
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    try:
        await store.create_run(running_id, "26.07d", _fingerprint())
        assert await store.fail_run(running_id, RuntimeError("first")) is True

        await store.create_run(already_failed_id, "26.07d", _fingerprint())
        claim = await store.claim_work_item(already_failed_id, "C0")
        assert claim is not None
        await store.fail_work_item(
            already_failed_id, "C0", claim, RuntimeError("item failed")
        )
        # Already demoted by fail_work_item: the failure *is* recorded.
        assert await store.fail_run(already_failed_id, RuntimeError("second")) is True

        await store.create_run(complete_id, "26.07d", _fingerprint())
        for code in ("C0", "C1"):
            done = await store.claim_work_item(complete_id, code)
            assert done is not None
            await store.complete_work_item(
                complete_id,
                code,
                done,
                decomposition=None,
                minted=(),
                outcome="atomic-no-op",
                semantic_types=("Neoplastic Process",),
            )
        await store.finish_run(
            complete_id,
            source_identity="a" * 64,
            metrics=await _completion_metrics(store, complete_id),
        )
        assert await store.fail_run(complete_id, RuntimeError("too late")) is False
    finally:
        await _cleanup([running_id, already_failed_id, complete_id])
        await dispose_engine(engine)


async def test_legacy_fingerprint_rows_fail_closed_on_resume() -> None:
    """Migration 0008 backfills pre-exact runs; resuming one must be a domain error.

    A raw pydantic ``ValidationError`` would leak the persistence schema through the
    store's public contract. ``resume_run`` runs before ``run_pipeline``'s failure
    handler, so neither error is recorded as a run failure — the caller can only
    react to a typed one.
    """
    run_id = _new_run_id("neoplasm")
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    conn = await asyncpg.connect(_dsn())
    try:
        # Insert the shape migration 0008 backfills. The identity trigger correctly
        # refuses to mutate an existing run's fingerprint, so seed it directly.
        await conn.execute(
            "INSERT INTO decomp_run (id, branch, status, ncit_version, started_at, "
            "source_identity, fingerprint, fingerprint_sha256, emitted_at, "
            "error_type, error_message, publication_state) VALUES "
            "($1, 'neoplasm', 'failed', '26.07d', now(), repeat('0', 64), "
            "jsonb_build_object('schema_version', 0, 'legacy', true, "
            "'run_id', $1::text), "
            "repeat('0', 64), now(), 'LegacyRun', "
            "'Legacy run predates exact worklist persistence', 'pending')",
            run_id,
        )

        with pytest.raises(RunIdentityMismatchError, match="predates"):
            await store.resume_run(
                run_id,
                RunResumeIdentity.from_fingerprint(_fingerprint()),
            )
    finally:
        await conn.close()
        await _cleanup([run_id])
        await dispose_engine(engine)


async def test_failed_then_resumed_run_matches_fresh_metrics_and_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_module, "_decompose_one", _InterruptedDecomposer())
    monkeypatch.setattr(run_module, "enumerate_in_scope_codes", _two_codes)
    monkeypatch.setattr(run_module, "_precoordinated_fillers", _no_residuals)

    engine = make_engine(get_settings().database_url)
    store = _RecordingStore(make_sessionmaker(engine))
    resumed_out = tmp_path / "resumed.ttl"
    fresh_out = tmp_path / "fresh.ttl"
    run_ids: list[str] = []
    try:
        with pytest.raises(RuntimeError, match="interruption"):
            await run_pipeline(
                RunConfig(branch="neoplasm", out=resumed_out),
                _LifecycleClient(),
                store,
                get_source_snapshot=_source,
            )
        interrupted_run = store.created[-1]
        run_ids.append(interrupted_run)

        resumed = await run_pipeline(
            RunConfig(
                branch="neoplasm",
                out=resumed_out,
                resume_from=interrupted_run,
            ),
            _LifecycleClient(),
            store,
            get_source_snapshot=_source,
        )
        fresh = await run_pipeline(
            RunConfig(branch="neoplasm", out=fresh_out),
            _LifecycleClient(),
            store,
            get_source_snapshot=_source,
        )
        fresh_run = store.created[-1]
        run_ids.append(fresh_run)

        assert resumed == fresh
        conn = await asyncpg.connect(_dsn())
        try:
            resumed_payload = await conn.fetchval(
                "SELECT metrics FROM decomp_run WHERE id = $1",
                interrupted_run,
            )
            fresh_payload = await conn.fetchval(
                "SELECT metrics FROM decomp_run WHERE id = $1",
                fresh_run,
            )
        finally:
            await conn.close()
        assert resumed_payload == fresh_payload
        assert set(json.loads(resumed_payload)) == {
            "total_in_scope",
            "decomposed",
            "residual",
            "semantic_excluded",
            "atomic_noop",
            "unknown_outcome",
            "residual_precoordinated_count",
            "residual_precoordination",
            "minted_count",
            "complete_definition_count",
            "complete_fact_count",
            "projected_fact_count",
            "projection_loss_count",
            "projection_loss_rate",
            "pct_decomposed",
            "roundtrip_fidelity",
        }
        assert await store.decompositions_for_run(
            interrupted_run
        ) == await store.decompositions_for_run(fresh_run)
        normalized_resumed = resumed_out.read_text().replace(
            interrupted_run, "<RUN_ID>"
        )
        normalized_fresh = fresh_out.read_text().replace(fresh_run, "<RUN_ID>")
        assert normalized_resumed == normalized_fresh
    finally:
        await _cleanup(run_ids)
        await dispose_engine(engine)
