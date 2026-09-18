"""Provenance persistence for decomposition runs (design section 4.5)."""

from __future__ import annotations

import asyncio
import datetime
import json as _json
import logging
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from sqlalchemy.engine import RowMapping
    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

    from ontolib.decomposition.minting import MintedConcept as MintedProposal
    from ontolib.decomposition.mixed_chain_inventory import (
        HistoricalMixedChainRunBinding,
        PersistedSelectorOccurrence,
    )
    from ontolib.decomposition.mixed_chain_projection import PersistedProjectionState
    from ontolib.decomposition.models import (
        CompleteDefinition,
        Constituent,
        Decomposition,
    )
    from ontolib.decomposition.r101_comparator import ComparatorRun
    from ontolib.decomposition.r101_conservation import (
        OccurrenceInput,
        R101LedgerSource,
    )

from ontolib.decomposition.models import (
    CompleteDefinition,
    ConceptOutcome,
    Constituent,
    Decomposition,
    DefinitionGroup,
    GenusDefinitionFact,
    OccurrenceDisposition,
    RestrictionDefinitionFact,
    SourceDefinitionOccurrence,
    SpecificityPathEdge,
)
from ontolib.decomposition.provenance_models import (
    RUN_STAGE_SEQUENCE,
    CompletedRunForEvidence,
    CompletionRunMetrics,
    CorpusBaselineAggregate,
    FreshAdmitted,
    FullRunExecutionIdentity,
    MintedConcept,
    PersistedRunMetrics,
    PublicationMarkerSnapshot,
    RefusalReason,
    Refused,
    ResidualFillerClassification,
    ResumeAdmitted,
    ResumeKind,
    RunAdmission,
    RunFingerprint,
    RunOutcomeCounts,
    RunResumeIdentity,
    RunStageCheckpoint,
    RunStageName,
    RunSummary,
    WorkItemOutcome,
    stage_output_identity,
)

_logger = logging.getLogger(__name__)
_MAX_BOUNDED_SELECTOR_CODES = 100


_PUBLICATION_LOCK_KEY = "decomposition:publication"
_SHA256_HEX_LENGTH = 64
_ADMISSION_STATE_KIND = {
    ("running", "not_requested"): "semantic",
    ("running", "pending"): "semantic",
    ("running", "publishing"): "publication",
    ("running", "failed"): "publication",
    ("failed", "not_requested"): "semantic",
    ("failed", "pending"): "semantic",
    ("failed", "failed"): "publication",
    ("complete", "legacy"): "complete",
    ("complete", "not_requested"): "complete",
    ("complete", "published"): "complete",
}


def _existing_run_refusal(kind: str) -> RefusalReason:
    if kind == "publication":
        return RefusalReason.PUBLICATION_RETRY_REQUIRED
    return RefusalReason.ACTIVE_RUN_EXISTS


class RunStateError(RuntimeError):
    """A requested run/work-item transition is not currently valid."""


class RunIdentityMismatchError(RuntimeError):
    """A resume or completion attempted to cross an immutable run identity."""


def _require_stage_inventory(rows: Sequence[RowMapping]) -> None:
    if tuple(row["stage"] for row in rows) != RUN_STAGE_SEQUENCE:
        raise RunIdentityMismatchError(
            "persisted run has no complete stage checkpoint inventory"
        )


def _completed_stage_matches(row: RowMapping, input_identity: str) -> bool:
    if row["state"] != "complete":
        return False
    if row["input_identity"] != input_identity:
        raise RunIdentityMismatchError("completed stage input identity does not match")
    return True


def _require_residual_context(
    persisted_source: object, source_identity: str, stage_state: object
) -> None:
    if persisted_source != source_identity:
        raise RunIdentityMismatchError("residual filler source identity differs")
    if stage_state != "running":
        raise RunStateError("residual filler initialization requires stage claim")


def _existing_residual_inventory_matches(
    existing: Sequence[RowMapping], expected: tuple[tuple[str, str, str], ...]
) -> bool:
    if not existing:
        return False
    actual = tuple(
        (row["filler_code"], row["source_identity"], row["detector_identity"])
        for row in existing
    )
    if actual != expected:
        raise RunIdentityMismatchError("residual filler inventory differs")
    return True


def _parse_r101_occurrences(rows: Sequence[RowMapping]) -> list[OccurrenceInput]:
    from ontolib.decomposition.r101_conservation import (  # noqa: PLC0415
        EngineOccurrenceDisposition,
        OccurrenceInput,
        Pair,
        R101ConservationValidationError,
        StructuralOccurrence,
    )

    occurrences: list[OccurrenceInput] = []
    for row in rows:
        if row["old_occurrence"] is None or row["new_occurrence"] is None:
            raise R101ConservationValidationError("structural-key-mismatch")
        old_payload = dict(row["old_occurrence"])
        new_payload = dict(row["new_occurrence"])
        old_payload["structural_path"] = tuple(old_payload["structural_path"])
        new_payload["structural_path"] = tuple(new_payload["structural_path"])
        retained_links = cast("list[dict[str, str]]", row["retained_links"])
        disposition = row["new_disposition"]
        occurrences.append(
            OccurrenceInput(
                old_occurrence=StructuralOccurrence.model_validate(old_payload),
                new_occurrence=StructuralOccurrence.model_validate(new_payload),
                old_links=tuple(row["old_links"]),
                new_links=tuple(row["new_links"]),
                retained_new_r101_links=tuple(
                    Pair.model_validate(item)
                    for item in sorted(
                        retained_links,
                        key=lambda item: (item["axis"], item["filler_code"]),
                    )
                ),
                new_disposition=(
                    EngineOccurrenceDisposition.model_validate(disposition)
                    if disposition is not None
                    else None
                ),
            )
        )
    return occurrences


def _require_completion_source(row: RowMapping, source_identity: str) -> None:
    if row["source_identity"] != source_identity:
        raise RunIdentityMismatchError(
            "completion source identity does not match persisted run"
        )


def _require_completion_publication(
    row: RowMapping,
    representation_identity: str | None,
    run_id: str,
) -> None:
    publication_state = row["publication_state"]
    if publication_state == "not_requested":
        if representation_identity is not None:
            raise RunIdentityMismatchError(
                "a non-publishing run cannot complete a representation"
            )
        return
    if publication_state == "publishing":
        if row["representation_identity"] != representation_identity:
            raise RunIdentityMismatchError(
                "completion representation identity does not match "
                "the publication intent"
            )
        return
    raise RunStateError(
        f"decomposition run {run_id!r} publication has not completed coordination "
        f"(state={publication_state!r})"
    )


def _bounded_failure(error: BaseException) -> tuple[str, str]:
    error_type = type(error).__name__[:128] or "Exception"
    message = str(error)[:1000] or error_type
    return error_type, message


async def _reopen_run(session: AsyncSession, run_id: str) -> None:
    """Reopen a running or failed run for one resuming worker.

    A hard kill (SIGKILL, OOM) leaves the run `running` with claimed items. Nothing
    distinguishes those from a live worker's claims, so this relies on the operating
    rule that one explicit resume is the run's only worker: a concurrent resume takes
    over the live claims and the older worker aborts at its next completion
    (`_require_owned_claim`).
    """
    await session.execute(
        text(
            "UPDATE decomp_run SET status='running',error_type=NULL,"
            "error_message=NULL WHERE id=:id AND status='failed'"
        ),
        {"id": run_id},
    )
    await session.execute(
        text(
            "UPDATE decomp_work_item SET state='failed',claim_token=NULL,"
            "claimed_at=NULL,error_type='InterruptedRun',"
            "error_message='Prior worker did not finish its claim',"
            "failed_at=:failed_at WHERE run_id=:id AND state='running'"
        ),
        {"id": run_id, "failed_at": datetime.datetime.now(datetime.UTC)},
    )


async def _promote_mint_proposals(
    session: AsyncSession, run_id: str, fingerprint: RunFingerprint
) -> None:
    """Copy a completed run's mint proposals into the global curator queue.

    A rehearsal mints the same deterministic proposal ids the real run will mint;
    promoting them would make the throwaway run their owner, so it never promotes.
    """
    if fingerprint.rehearsal_nonce is not None:
        return
    await session.execute(
        text(
            "INSERT INTO minted_concept "
            "(id, run_id, axis, label, source_signal, status) "
            "SELECT proposal_id, run_id, axis, label, source_signal, status "
            "FROM decomp_minted_proposal WHERE run_id = :id "
            # Insert-or-ignore, never insert-or-update: a rerun re-mints the same
            # deterministic proposal id with status='proposed', and promotion must
            # never clobber a curator's earlier approve or reject decision (design
            # section 7.2).
            "ON CONFLICT (id) DO NOTHING"
        ),
        {"id": run_id},
    )


def _invalid_fingerprint_detail(raw: object, persisted_identity: str) -> str:
    """Classify known historical schemas without laundering arbitrary corruption."""
    if not isinstance(raw, dict):
        return "is corrupt or was modified outside the pipeline"
    schema_version = raw.get("schema_version")
    if schema_version == 0 and persisted_identity == "0" * 64:
        return "predates the exact-run schema"
    if schema_version == 1:
        return "predates the hierarchy-scope schema"
    return "is corrupt or was modified outside the pipeline"


async def _invalidate_without_masking(
    connection: AsyncConnection,
    original: BaseException,
) -> None:
    try:
        await connection.invalidate()
    except BaseException as invalidation_error:
        original.add_note(
            "Invalidating the publication-lock connection also failed: "
            f"{type(invalidation_error).__name__}: {invalidation_error}"
        )


async def _acquire_publication_lock(connection: AsyncConnection) -> None:
    task = asyncio.create_task(
        connection.execute(
            text("SELECT pg_advisory_lock(hashtextextended(:key, 0))"),
            {"key": _PUBLICATION_LOCK_KEY},
        )
    )
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError as cancelled:
        try:
            await task
        except BaseException as acquisition_error:
            cancelled.add_note(
                "Publication-lock acquisition also failed after cancellation: "
                f"{type(acquisition_error).__name__}: {acquisition_error}"
            )
            await _invalidate_without_masking(connection, cancelled)
            raise cancelled from acquisition_error
        try:
            await _release_publication_lock(connection)
        except BaseException as unlock_error:
            cancelled.add_note(
                "Failed to release decomposition publication lock after "
                f"cancellation: {unlock_error}"
            )
            await _invalidate_without_masking(connection, cancelled)
        raise


async def _release_publication_lock(connection: AsyncConnection) -> None:
    async def release() -> None:
        unlocked = await connection.scalar(
            text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"),
            {"key": _PUBLICATION_LOCK_KEY},
        )
        await connection.commit()
        if not unlocked:
            raise RunStateError("failed to release decomposition publication lock")

    task = asyncio.create_task(release())
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError as cancelled:
        try:
            await task
        except BaseException as release_error:
            cancelled.add_note(
                "Publication-lock release also failed after cancellation: "
                f"{type(release_error).__name__}: {release_error}"
            )
            await _invalidate_without_masking(connection, cancelled)
            raise cancelled from release_error
        raise


def _residual_precoordination_metric(
    metrics: dict[str, object],
) -> float | None:
    """Read a stored rate or derive it for count-only historical run rows."""
    if metrics.get("residual_precoordination_unknown_count"):
        return None
    rate = metrics.get("residual_precoordination")
    if rate is not None:
        return cast("float", rate)
    count = metrics.get("residual_precoordinated_count")
    decomposed = metrics.get("decomposed")
    if isinstance(count, int) and isinstance(decomposed, int):
        return count / decomposed if decomposed else 0.0
    return None


def _validated_metrics(metrics: object) -> PersistedRunMetrics:
    if isinstance(metrics, str):
        try:
            metrics = _json.loads(metrics)
        except _json.JSONDecodeError as exc:
            raise RunStateError("persisted run metrics are not valid JSON") from exc
    if not isinstance(metrics, dict):
        raise RunStateError("persisted run metrics are not a JSON object")
    normalized = dict(metrics)
    if normalized.get("residual_precoordination") is None:
        derived = _residual_precoordination_metric(normalized)
        if derived is not None:
            normalized["residual_precoordination"] = derived
    try:
        validated = PersistedRunMetrics.model_validate(normalized)
    except ValidationError as exc:
        raise RunStateError("persisted run metrics violate their schema") from exc
    return validated


def _validate_publication_retry(
    row: RowMapping,
    *,
    state: str,
    requested_identity: tuple[str, str, datetime.datetime],
    requested_predecessor: dict[str, object] | None,
) -> None:
    if state == "pending":
        return
    persisted_identity = (
        row["representation_identity"],
        row["publication_artifact_path"],
        row["publication_built_at"],
    )
    if persisted_identity != requested_identity:
        raise RunIdentityMismatchError(
            "publication representation, destination, or build time "
            "does not match the persisted intent"
        )
    if not row["publication_predecessor_captured"]:
        raise RunStateError(
            "publication intent predates predecessor capture and cannot be retried "
            "safely"
        )
    if row["publication_predecessor"] != requested_predecessor:
        raise RunIdentityMismatchError(
            "publication predecessor does not match the persisted intent"
        )


def _completion_outcome(
    concept_code: str,
    decomposition: Decomposition | None,
    minted: tuple[MintedProposal, ...],
) -> tuple[list[Constituent], bool, bool]:
    _require_matching_decomposition(concept_code, decomposition)
    _require_decomposition_for_mints(decomposition, minted)
    constituents = list(decomposition.constituents) if decomposition is not None else []
    has_decomposition = decomposition is not None
    return (
        constituents,
        has_decomposition and bool(constituents),
        has_decomposition and not constituents,
    )


def _expected_completion_outcome(
    decomposition: Decomposition | None,
    outcome: ConceptOutcome | None,
    *,
    is_decomposed: bool,
    is_residual: bool,
) -> ConceptOutcome:
    if is_decomposed:
        return "decomposed"
    if is_residual:
        return "residual"
    if decomposition is None:
        if outcome in {"semantic-excluded", "atomic-no-op", "unknown"}:
            return outcome
        raise RunStateError(
            "non-decomposition completion requires an explicit typed outcome"
        )
    raise RunStateError("completion outcome does not match decomposition result")


def _canonical_completion_semantic_types(
    decomposition: Decomposition | None,
    semantic_types: tuple[str, ...],
) -> tuple[str, ...]:
    canonical = tuple(sorted(set(semantic_types)))
    if any(not value for value in canonical):
        raise RunStateError("completion semantic types must be non-empty strings")
    representative = decomposition.semantic_type if decomposition is not None else None
    if representative is not None and representative not in canonical:
        raise RunStateError(
            "representative semantic type must occur in completion semantic types"
        )
    return canonical


def _validated_completion_metadata(
    decomposition: Decomposition | None,
    outcome: ConceptOutcome | None,
    semantic_types: tuple[str, ...],
    *,
    is_decomposed: bool,
    is_residual: bool,
) -> tuple[ConceptOutcome, tuple[str, ...], CompleteDefinition | None]:
    expected = _expected_completion_outcome(
        decomposition,
        outcome,
        is_decomposed=is_decomposed,
        is_residual=is_residual,
    )
    resolved = expected if outcome is None else outcome
    if resolved != expected:
        raise RunStateError("completion outcome does not match decomposition result")
    return (
        resolved,
        _canonical_completion_semantic_types(decomposition, semantic_types),
        decomposition.complete_definition if decomposition is not None else None,
    )


def _require_matching_decomposition(
    concept_code: str,
    decomposition: Decomposition | None,
) -> None:
    if decomposition is not None and decomposition.code != concept_code:
        raise ValueError("decomposition code does not match the claimed work item")


def _require_decomposition_for_mints(
    decomposition: Decomposition | None,
    minted: tuple[MintedProposal, ...],
) -> None:
    if decomposition is None and minted:
        raise ValueError("minted proposals require a decomposition")


def _require_owned_claim(
    row: RowMapping | None,
    run_id: str,
    concept_code: str,
    claim_token: UUID,
) -> None:
    actual = (
        None if row is None else (row["state"], str(row["claim_token"]), row["status"])
    )
    expected = ("running", str(claim_token), "running")
    if actual != expected:
        raise RunStateError(
            f"work item {run_id!r}/{concept_code!r} is not owned by this claim"
        )


def _constituent_rows(
    run_id: str,
    concept_code: str,
    constituents: list[Constituent],
) -> list[dict[str, object]]:
    return [
        {
            "run_id": run_id,
            "concept_code": concept_code,
            "axis": constituent.axis,
            "filler_code": constituent.filler_code,
            "axis_source": constituent.axis_source,
            "source_roles": _json.dumps(
                constituent.source_roles,
                separators=(",", ":"),
            ),
            "most_specific": constituent.most_specific,
            "needs_review": constituent.needs_review,
            "axis_ambiguity_group_id": constituent.axis_ambiguity_group_id,
            "source_group_ids": _json.dumps(
                constituent.source_group_ids, separators=(",", ":")
            ),
            "normalized_group_id": constituent.normalized_group_id,
            "normalized_group_label": constituent.normalized_group_label,
            "source_definition_ids": _json.dumps(
                constituent.source_definition_ids,
                separators=(",", ":"),
            ),
        }
        for constituent in constituents
    ]


def _definition_fact_rows(
    run_id: str,
    concept_code: str,
    complete_definition: CompleteDefinition | None,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    if complete_definition is None:
        return rows
    for fact in complete_definition.facts:
        common: dict[str, object] = {
            "run_id": run_id,
            "concept_code": concept_code,
            "fact_id": fact.fact_id,
            "anchor_code": fact.anchor_code,
            "group_id": fact.group_id,
            "depth": fact.depth,
        }
        if isinstance(fact, GenusDefinitionFact):
            rows.append(
                common
                | {
                    "fact_kind": "genus",
                    "genus_code": fact.genus_code,
                    "is_defined": fact.is_defined,
                    "role_code": None,
                    "filler_code": None,
                }
            )
        else:
            rows.append(
                common
                | {
                    "fact_kind": "restriction",
                    "genus_code": None,
                    "is_defined": None,
                    "role_code": fact.role_code,
                    "filler_code": fact.filler_code,
                }
            )
    return rows


def _definition_group_rows(
    run_id: str,
    concept_code: str,
    complete_definition: CompleteDefinition | None,
) -> list[dict[str, object]]:
    if complete_definition is None:
        return []
    roots = set(complete_definition.root_group_ids)
    return [
        {
            "run_id": run_id,
            "concept_code": concept_code,
            "group_id": group.group_id,
            "anchor_code": group.anchor_code,
            "depth": group.depth,
            "is_root": group.group_id in roots,
        }
        for group in complete_definition.groups
    ]


def _definition_group_edge_rows(
    run_id: str,
    concept_code: str,
    complete_definition: CompleteDefinition | None,
) -> list[dict[str, object]]:
    if complete_definition is None:
        return []
    return [
        {
            "run_id": run_id,
            "concept_code": concept_code,
            "parent_group_id": group.group_id,
            "child_group_id": child_group_id,
        }
        for group in complete_definition.groups
        for child_group_id in group.child_group_ids
    ]


def _source_occurrence_rows(
    run_id: str,
    concept_code: str,
    complete_definition: CompleteDefinition | None,
) -> list[dict[str, object]]:
    if complete_definition is None:
        return []
    return [
        {
            "run_id": run_id,
            "concept_code": concept_code,
            "occurrence_id": occurrence.occurrence_id,
            "source_fact_id": occurrence.source_fact_id,
            "source_group_id": occurrence.source_group_id,
            "anchor_code": occurrence.anchor_code,
            "depth": occurrence.depth,
            "role_code": occurrence.role_code,
            "filler_code": occurrence.filler_code,
            "structural_path": list(occurrence.structural_path),
            "member_position": occurrence.member_position,
        }
        for occurrence in complete_definition.occurrences
    ]


def _constituent_occurrence_rows(
    run_id: str,
    concept_code: str,
    constituents: list[Constituent],
) -> list[dict[str, object]]:
    return [
        {
            "run_id": run_id,
            "concept_code": concept_code,
            "axis": constituent.axis,
            "filler_code": constituent.filler_code,
            "occurrence_id": occurrence_id,
        }
        for constituent in constituents
        for occurrence_id in constituent.source_occurrence_ids
    ]


def _occurrence_disposition_rows(
    run_id: str,
    concept_code: str,
    dispositions: Sequence[OccurrenceDisposition],
) -> list[dict[str, object]]:
    return [
        {
            "run_id": run_id,
            "concept_code": concept_code,
            "occurrence_id": row.source_occurrence_id,
            "source_fact_id": row.source_fact_id,
            "disposition": row.kind,
            "normalized_axis": row.normalized_axis,
            "source_filler": row.source_filler,
            "retained_filler": row.retained_filler,
            "semantic_route": row.semantic_route,
            "semantic_type": row.semantic_type,
            "r82_part": row.r82_part,
            "r82_whole": row.r82_whole,
            "specificity_path": _json.dumps(
                [asdict(edge) for edge in row.specificity_path],
                sort_keys=True,
                separators=(",", ":"),
            ),
            "policy_decision_identity": row.policy_decision_identity,
        }
        for row in dispositions
    ]


def _proposal_rows(
    run_id: str,
    concept_code: str,
    minted: tuple[MintedProposal, ...],
) -> list[dict[str, object]]:
    return [
        {
            "run_id": run_id,
            "concept_code": concept_code,
            "proposal_id": proposal.id,
            "axis": proposal.axis,
            "label": proposal.label,
            "source_signal": proposal.source_signal,
            "status": proposal.status,
        }
        for proposal in minted
    ]


async def _delete_completion_rows(
    session: AsyncSession,
    run_id: str,
    concept_code: str,
) -> None:
    params = {"run_id": run_id, "concept_code": concept_code}
    for statement in (
        "DELETE FROM decomp_occurrence_disposition "
        "WHERE run_id = :run_id AND concept_code = :concept_code",
        "DELETE FROM decomp_constituent_occurrence "
        "WHERE run_id = :run_id AND concept_code = :concept_code",
        "DELETE FROM decomp_source_occurrence "
        "WHERE run_id = :run_id AND concept_code = :concept_code",
        "DELETE FROM decomp_definition_fact "
        "WHERE run_id = :run_id AND concept_code = :concept_code",
        "DELETE FROM decomp_definition_group_edge "
        "WHERE run_id = :run_id AND concept_code = :concept_code",
        "DELETE FROM decomp_definition_group "
        "WHERE run_id = :run_id AND concept_code = :concept_code",
        "DELETE FROM decomp_constituent "
        "WHERE run_id = :run_id AND concept_code = :concept_code",
        "DELETE FROM decomp_minted_proposal "
        "WHERE run_id = :run_id AND concept_code = :concept_code",
    ):
        await session.execute(text(statement), params)


async def _insert_completion_rows(
    session: AsyncSession,
    statement: str,
    rows: list[dict[str, object]],
) -> None:
    if rows:
        await session.execute(text(statement), rows)


async def _persist_completion_rows(
    session: AsyncSession,
    run_id: str,
    concept_code: str,
    constituents: list[Constituent],
    complete_definition: CompleteDefinition | None,
    dispositions: Sequence[OccurrenceDisposition],
    minted: tuple[MintedProposal, ...],
) -> None:
    await _insert_completion_rows(
        session,
        "INSERT INTO decomp_constituent "
        "(run_id, concept_code, axis, filler_code, axis_source, source_roles, "
        "most_specific, needs_review, axis_ambiguity_group_id, source_group_ids, "
        "normalized_group_id, normalized_group_label, source_definition_ids) "
        "VALUES (:run_id, :concept_code, :axis, :filler_code, :axis_source, "
        "CAST(:source_roles AS jsonb), :most_specific, :needs_review, "
        ":axis_ambiguity_group_id, CAST(:source_group_ids AS jsonb), "
        ":normalized_group_id, :normalized_group_label, "
        "CAST(:source_definition_ids AS jsonb))",
        _constituent_rows(run_id, concept_code, constituents),
    )
    await _insert_completion_rows(
        session,
        "INSERT INTO decomp_definition_group "
        "(run_id, concept_code, group_id, anchor_code, depth, is_root) "
        "VALUES (:run_id, :concept_code, :group_id, :anchor_code, :depth, :is_root)",
        _definition_group_rows(run_id, concept_code, complete_definition),
    )
    await _insert_completion_rows(
        session,
        "INSERT INTO decomp_definition_group_edge "
        "(run_id, concept_code, parent_group_id, child_group_id) "
        "VALUES (:run_id, :concept_code, :parent_group_id, :child_group_id)",
        _definition_group_edge_rows(run_id, concept_code, complete_definition),
    )
    await _insert_completion_rows(
        session,
        "INSERT INTO decomp_definition_fact "
        "(run_id, concept_code, fact_id, anchor_code, group_id, depth, fact_kind, "
        "genus_code, is_defined, role_code, filler_code) "
        "VALUES (:run_id, :concept_code, :fact_id, :anchor_code, :group_id, "
        ":depth, :fact_kind, :genus_code, :is_defined, :role_code, :filler_code)",
        _definition_fact_rows(run_id, concept_code, complete_definition),
    )
    await _insert_completion_rows(
        session,
        "INSERT INTO decomp_source_occurrence "
        "(run_id, concept_code, occurrence_id, source_fact_id, source_group_id, "
        "anchor_code, depth, role_code, filler_code, structural_path, "
        "member_position) VALUES (:run_id, :concept_code, :occurrence_id, "
        ":source_fact_id, :source_group_id, :anchor_code, :depth, :role_code, "
        ":filler_code, :structural_path, :member_position)",
        _source_occurrence_rows(run_id, concept_code, complete_definition),
    )
    await _insert_completion_rows(
        session,
        "INSERT INTO decomp_occurrence_disposition "
        "(run_id, concept_code, occurrence_id, source_fact_id, disposition, "
        "normalized_axis, source_filler, retained_filler, semantic_route, "
        "semantic_type, r82_part, r82_whole, specificity_path, "
        "policy_decision_identity) VALUES "
        "(:run_id, :concept_code, :occurrence_id, :source_fact_id, :disposition, "
        ":normalized_axis, :source_filler, :retained_filler, :semantic_route, "
        ":semantic_type, :r82_part, :r82_whole, CAST(:specificity_path AS jsonb), "
        ":policy_decision_identity)",
        _occurrence_disposition_rows(run_id, concept_code, dispositions),
    )
    await _insert_completion_rows(
        session,
        "INSERT INTO decomp_constituent_occurrence "
        "(run_id, concept_code, axis, filler_code, occurrence_id) VALUES "
        "(:run_id, :concept_code, :axis, :filler_code, :occurrence_id)",
        _constituent_occurrence_rows(run_id, concept_code, constituents),
    )
    await _insert_completion_rows(
        session,
        "INSERT INTO decomp_minted_proposal "
        "(run_id, concept_code, proposal_id, axis, label, source_signal, status) "
        "VALUES (:run_id, :concept_code, :proposal_id, :axis, :label, "
        ":source_signal, :status)",
        _proposal_rows(run_id, concept_code, minted),
    )


async def _require_persisted_completion_counts(
    session: AsyncSession,
    run_id: str,
) -> None:
    result = await session.execute(
        text(
            "SELECT w.concept_code, w.constituent_count, w.minted_count, "
            "actual_constituents.value AS actual_constituent_count, "
            "actual_mints.value AS actual_minted_count "
            "FROM decomp_work_item w "
            "CROSS JOIN LATERAL (SELECT count(*)::integer AS value "
            "FROM decomp_constituent c WHERE c.run_id = w.run_id "
            "AND c.concept_code = w.concept_code) actual_constituents "
            "CROSS JOIN LATERAL (SELECT count(*)::integer AS value "
            "FROM decomp_minted_proposal m WHERE m.run_id = w.run_id "
            "AND m.concept_code = w.concept_code) actual_mints "
            "WHERE w.run_id = :run_id AND w.state = 'complete' AND ("
            "w.constituent_count IS DISTINCT FROM actual_constituents.value OR "
            "w.minted_count IS DISTINCT FROM actual_mints.value) "
            "ORDER BY w.ordinal LIMIT 1"
        ),
        {"run_id": run_id},
    )
    row = result.mappings().first()
    if row is not None:
        raise RunStateError(
            f"persisted completion counts do not match child rows for "
            f"{run_id!r}/{row['concept_code']!r} "
            f"(constituents={row['constituent_count']}/"
            f"{row['actual_constituent_count']}, "
            f"mints={row['minted_count']}/{row['actual_minted_count']})"
        )


async def _persisted_outcome_counts(
    session: AsyncSession,
    run_id: str,
) -> RunOutcomeCounts:
    result = await session.execute(
        text(
            "SELECT count(*) AS total_in_scope, "
            "count(*) FILTER (WHERE is_decomposed) AS decomposed, "
            "count(*) FILTER (WHERE is_residual) AS residual, "
            "count(*) FILTER (WHERE outcome = 'semantic-excluded') "
            "AS semantic_excluded, "
            "count(*) FILTER (WHERE outcome = 'atomic-no-op') AS atomic_noop, "
            "count(*) FILTER (WHERE outcome = 'unknown') AS unknown_outcome, "
            "COALESCE(sum(minted_count), 0) AS minted_count "
            "FROM decomp_work_item WHERE run_id = :run_id"
        ),
        {"run_id": run_id},
    )
    return RunOutcomeCounts.model_validate(dict(result.mappings().one()))


async def _persisted_definition_counts(
    session: AsyncSession,
    run_id: str,
) -> tuple[int, int, int]:
    """Recompute the definition metrics the way the pipeline computes them.

    Two scoping rules must match :func:`decompositions_for_run` exactly, or
    :func:`_require_matching_completion_metrics` rejects every well-formed run:

    * only ``is_decomposed`` work items contribute. A ``residual`` concept still
      carries a complete definition and its facts are persisted, but it is absent
      from the reconstructed decompositions the pipeline sums over.
    * ``projected_fact_count`` is distinct *within* a concept and then summed, not
      distinct across the run. ``fact_id`` is anchored on the expression's own
      concept, so two roots sharing a defined genus legitimately reference the
      same fact id twice.
    """
    result = await session.execute(
        text(
            "SELECT "
            "(SELECT count(*) FROM decomp_work_item "
            "WHERE run_id = :run_id AND has_complete_definition "
            "AND is_decomposed) AS complete_definition_count, "
            "(SELECT count(*) FROM decomp_definition_fact f "
            "JOIN decomp_work_item w ON w.run_id = f.run_id "
            "AND w.concept_code = f.concept_code "
            "WHERE f.run_id = :run_id AND w.is_decomposed) AS complete_fact_count, "
            "(SELECT COALESCE(sum(per_concept), 0) FROM ("
            "SELECT count(DISTINCT source_id.value) AS per_concept "
            "FROM decomp_constituent c "
            "JOIN decomp_work_item w ON w.run_id = c.run_id "
            "AND w.concept_code = c.concept_code "
            "CROSS JOIN LATERAL jsonb_array_elements_text(c.source_definition_ids) "
            "AS source_id(value) WHERE c.run_id = :run_id AND w.is_decomposed "
            "GROUP BY c.concept_code) AS per_concept_counts) AS projected_fact_count"
        ),
        {"run_id": run_id},
    )
    row = result.mappings().one()
    return (
        row["complete_definition_count"],
        row["complete_fact_count"],
        row["projected_fact_count"],
    )


def _require_matching_completion_metrics(
    metrics: CompletionRunMetrics,
    counts: RunOutcomeCounts,
    definition_counts: tuple[int, int, int],
) -> None:
    persisted_counts = (
        counts.total_in_scope,
        counts.decomposed,
        counts.residual,
        counts.semantic_excluded,
        counts.atomic_noop,
        counts.unknown_outcome,
        counts.minted_count,
    )
    supplied_counts = (
        metrics.total_in_scope,
        metrics.decomposed,
        metrics.residual,
        metrics.semantic_excluded,
        metrics.atomic_noop,
        metrics.unknown_outcome,
        metrics.minted_count,
    )
    if persisted_counts != supplied_counts:
        raise RunStateError(
            "completion metrics do not match persisted work-item outcomes"
        )
    complete_definition_count, complete_fact_count, projected_fact_count = (
        definition_counts
    )
    persisted_definition_metrics = (
        complete_definition_count,
        complete_fact_count,
        projected_fact_count,
        complete_fact_count - projected_fact_count,
    )
    supplied_definition_metrics = (
        metrics.complete_definition_count,
        metrics.complete_fact_count,
        metrics.projected_fact_count,
        metrics.projection_loss_count,
    )
    if persisted_definition_metrics != supplied_definition_metrics:
        raise RunStateError(
            "completion definition metrics do not match persisted definition rows"
        )


async def _finish_run_committed(
    sf: async_sessionmaker[AsyncSession],
    run_id: str,
    *,
    source_identity: str,
    metrics: dict[str, object],
    representation_identity: str | None,
    original: Exception,
) -> bool:
    try:
        async with sf() as session:
            result = await session.execute(
                text(
                    "SELECT status, source_identity, metrics, publication_state, "
                    "representation_identity FROM decomp_run WHERE id = :id"
                ),
                {"id": run_id},
            )
            row = result.mappings().first()
    except asyncio.CancelledError:
        raise
    except BaseException as reconciliation_error:
        original.add_note(
            "Reading the decomposition run during commit reconciliation also failed: "
            f"{type(reconciliation_error).__name__}: {reconciliation_error}"
        )
        raise original from reconciliation_error

    expected_metrics = _json.loads(_json.dumps(metrics))
    expected_publication_state = (
        "published" if representation_identity is not None else "not_requested"
    )
    return row is not None and (
        row["status"],
        row["source_identity"],
        row["metrics"],
        row["publication_state"],
        row["representation_identity"],
    ) == (
        "complete",
        source_identity,
        expected_metrics,
        expected_publication_state,
        representation_identity,
    )


async def _reconcile_finish_run_error(
    sf: async_sessionmaker[AsyncSession],
    run_id: str,
    *,
    updated: bool,
    source_identity: str,
    metrics: dict[str, object],
    representation_identity: str | None,
    original: Exception,
) -> bool:
    if not updated:
        return False
    return await _finish_run_committed(
        sf,
        run_id,
        source_identity=source_identity,
        metrics=metrics,
        representation_identity=representation_identity,
        original=original,
    )


async def _mark_work_item_complete(
    session: AsyncSession,
    run_id: str,
    concept_code: str,
    claim_token: UUID,
    decomposition: Decomposition | None,
    outcome: ConceptOutcome,
    semantic_types: tuple[str, ...],
    constituents: list[Constituent],
    minted: tuple[MintedProposal, ...],
    *,
    is_decomposed: bool,
    is_residual: bool,
) -> None:
    updated = await session.execute(
        text(
            "UPDATE decomp_work_item SET state = 'complete', "
            "claim_token = NULL, claimed_at = NULL, semantic_type = :semantic_type, "
            "semantic_types = CAST(:semantic_types AS jsonb), outcome = :outcome, "
            "is_decomposed = :is_decomposed, is_residual = :is_residual, "
            "has_complete_definition = :has_complete_definition, "
            "constituent_count = :constituent_count, minted_count = :minted_count, "
            "completed_at = :completed_at "
            "WHERE run_id = :run_id AND concept_code = :concept_code "
            "AND state = 'running' AND claim_token = :claim_token"
        ),
        {
            "run_id": run_id,
            "concept_code": concept_code,
            "claim_token": claim_token,
            "semantic_type": (
                decomposition.semantic_type
                if decomposition is not None
                else (semantic_types[0] if semantic_types else None)
            ),
            "semantic_types": _json.dumps(semantic_types),
            "outcome": outcome,
            "is_decomposed": is_decomposed,
            "is_residual": is_residual,
            "has_complete_definition": (
                decomposition is not None
                and decomposition.complete_definition is not None
            ),
            "constituent_count": len(constituents),
            "minted_count": len(minted),
            "completed_at": datetime.datetime.now(datetime.UTC),
        },
    )
    if not cast("int", updated.rowcount):  # type: ignore[attr-defined]
        raise RunStateError("work-item claim changed before completion")


async def _load_decomposition_rows(
    session: AsyncSession,
    run_id: str,
) -> tuple[
    Sequence[RowMapping],
    Sequence[RowMapping],
    Sequence[RowMapping],
    Sequence[RowMapping],
    Sequence[RowMapping],
    Sequence[RowMapping],
    Sequence[RowMapping],
    Sequence[RowMapping],
]:
    work_items = await session.execute(
        text(
            "SELECT concept_code, semantic_type, has_complete_definition "
            "FROM decomp_work_item "
            "WHERE run_id = :run_id AND state = 'complete' "
            "AND is_decomposed ORDER BY ordinal"
        ),
        {"run_id": run_id},
    )
    constituent_result = await session.execute(
        text(
            "SELECT concept_code, axis, filler_code, axis_source, source_roles, "
            "most_specific, needs_review, axis_ambiguity_group_id, source_group_ids, "
            "normalized_group_id, normalized_group_label, source_definition_ids "
            "FROM decomp_constituent WHERE run_id = :run_id "
            "ORDER BY concept_code, axis, filler_code"
        ),
        {"run_id": run_id},
    )
    definition_result = await session.execute(
        text(
            "SELECT concept_code, fact_id, anchor_code, group_id, depth, "
            "fact_kind, genus_code, is_defined, role_code, filler_code "
            "FROM decomp_definition_fact WHERE run_id = :run_id "
            "ORDER BY concept_code, fact_id"
        ),
        {"run_id": run_id},
    )
    group_result = await session.execute(
        text(
            "SELECT concept_code, group_id, anchor_code, depth, is_root "
            "FROM decomp_definition_group WHERE run_id = :run_id "
            "ORDER BY concept_code, group_id"
        ),
        {"run_id": run_id},
    )
    edge_result = await session.execute(
        text(
            "SELECT concept_code, parent_group_id, child_group_id "
            "FROM decomp_definition_group_edge WHERE run_id = :run_id "
            "ORDER BY concept_code, parent_group_id, child_group_id"
        ),
        {"run_id": run_id},
    )
    occurrence_result = await session.execute(
        text(
            "SELECT concept_code, occurrence_id, source_fact_id, source_group_id, "
            "anchor_code, depth, role_code, filler_code, structural_path, "
            "member_position FROM decomp_source_occurrence WHERE run_id = :run_id "
            "ORDER BY concept_code, occurrence_id"
        ),
        {"run_id": run_id},
    )
    occurrence_link_result = await session.execute(
        text(
            "SELECT concept_code, axis, filler_code, occurrence_id "
            "FROM decomp_constituent_occurrence WHERE run_id = :run_id "
            "ORDER BY concept_code, axis, filler_code, occurrence_id"
        ),
        {"run_id": run_id},
    )
    disposition_result = await session.execute(
        text(
            "SELECT concept_code, occurrence_id, source_fact_id, disposition, "
            "normalized_axis, source_filler, retained_filler, semantic_route, "
            "semantic_type, r82_part, r82_whole, specificity_path, "
            "policy_decision_identity "
            "FROM decomp_occurrence_disposition WHERE run_id = :run_id "
            "ORDER BY concept_code, occurrence_id"
        ),
        {"run_id": run_id},
    )
    return (
        work_items.mappings().all(),
        constituent_result.mappings().all(),
        definition_result.mappings().all(),
        group_result.mappings().all(),
        edge_result.mappings().all(),
        occurrence_result.mappings().all(),
        occurrence_link_result.mappings().all(),
        disposition_result.mappings().all(),
    )


def _constituents_by_code(
    rows: Sequence[RowMapping],
    occurrence_link_rows: Sequence[RowMapping],
) -> dict[str, list[Constituent]]:
    by_code: dict[str, list[Constituent]] = {}
    occurrence_ids_by_constituent: dict[tuple[str, str, str], list[str]] = {}
    for link in occurrence_link_rows:
        occurrence_ids_by_constituent.setdefault(
            (link["concept_code"], link["axis"], link["filler_code"]), []
        ).append(link["occurrence_id"])
    for row in rows:
        raw_source_ids = row["source_definition_ids"]
        if isinstance(raw_source_ids, str):
            raw_source_ids = _json.loads(raw_source_ids)
        raw_source_roles = row["source_roles"]
        if isinstance(raw_source_roles, str):
            raw_source_roles = _json.loads(raw_source_roles)
        raw_source_group_ids = row["source_group_ids"]
        if isinstance(raw_source_group_ids, str):
            raw_source_group_ids = _json.loads(raw_source_group_ids)
        by_code.setdefault(row["concept_code"], []).append(
            Constituent(
                axis=row["axis"],
                filler_code=row["filler_code"],
                axis_source=row["axis_source"],
                source_roles=tuple(raw_source_roles),
                most_specific=row["most_specific"],
                needs_review=row["needs_review"],
                axis_ambiguity_group_id=row["axis_ambiguity_group_id"],
                source_group_ids=tuple(raw_source_group_ids),
                normalized_group_id=row["normalized_group_id"],
                normalized_group_label=row["normalized_group_label"],
                source_definition_ids=tuple(raw_source_ids),
                source_occurrence_ids=tuple(
                    occurrence_ids_by_constituent.get(
                        (row["concept_code"], row["axis"], row["filler_code"]), []
                    )
                ),
            )
        )
    return by_code


def _definition_fact_from_row(
    row: RowMapping,
) -> GenusDefinitionFact | RestrictionDefinitionFact:
    common = {
        "fact_id": row["fact_id"],
        "anchor_code": row["anchor_code"],
        "group_id": row["group_id"],
        "depth": row["depth"],
    }
    if row["fact_kind"] == "genus":
        return GenusDefinitionFact(
            **common,
            genus_code=row["genus_code"],
            is_defined=row["is_defined"],
        )
    return RestrictionDefinitionFact(
        **common,
        role_code=row["role_code"],
        filler_code=row["filler_code"],
    )


def _definition_facts_by_code(
    rows: Sequence[RowMapping],
) -> dict[str, list[GenusDefinitionFact | RestrictionDefinitionFact]]:
    by_code: dict[str, list[GenusDefinitionFact | RestrictionDefinitionFact]] = {}
    for row in rows:
        by_code.setdefault(row["concept_code"], []).append(
            _definition_fact_from_row(row)
        )
    return by_code


def _definition_groups_by_code(
    group_rows: Sequence[RowMapping],
    edge_rows: Sequence[RowMapping],
) -> tuple[dict[str, list[DefinitionGroup]], dict[str, list[str]]]:
    children_by_group: dict[tuple[str, str], list[str]] = {}
    for row in edge_rows:
        children_by_group.setdefault(
            (row["concept_code"], row["parent_group_id"]),
            [],
        ).append(row["child_group_id"])
    groups_by_code: dict[str, list[DefinitionGroup]] = {}
    roots_by_code: dict[str, list[str]] = {}
    for row in group_rows:
        concept_code = row["concept_code"]
        group_id = row["group_id"]
        groups_by_code.setdefault(concept_code, []).append(
            DefinitionGroup(
                group_id=group_id,
                anchor_code=row["anchor_code"],
                depth=row["depth"],
                child_group_ids=tuple(
                    children_by_group.get((concept_code, group_id), [])
                ),
            )
        )
        if row["is_root"]:
            roots_by_code.setdefault(concept_code, []).append(group_id)
    return groups_by_code, roots_by_code


def _complete_definition_for_code(
    concept_code: str,
    has_complete_definition: bool,
    facts_by_code: dict[str, list[GenusDefinitionFact | RestrictionDefinitionFact]],
    groups_by_code: dict[str, list[DefinitionGroup]],
    roots_by_code: dict[str, list[str]],
    occurrences_by_code: dict[str, list[SourceDefinitionOccurrence]],
) -> CompleteDefinition | None:
    if not has_complete_definition:
        return None
    return CompleteDefinition(
        root_code=concept_code,
        facts=tuple(facts_by_code.get(concept_code, [])),
        groups=tuple(groups_by_code.get(concept_code, [])),
        root_group_ids=tuple(roots_by_code.get(concept_code, [])),
        occurrences=tuple(occurrences_by_code.get(concept_code, [])),
    )


def _occurrences_by_code(
    rows: Sequence[RowMapping],
) -> dict[str, list[SourceDefinitionOccurrence]]:
    by_code: dict[str, list[SourceDefinitionOccurrence]] = {}
    for row in rows:
        by_code.setdefault(row["concept_code"], []).append(
            SourceDefinitionOccurrence(
                occurrence_id=row["occurrence_id"],
                root_code=row["concept_code"],
                source_fact_id=row["source_fact_id"],
                source_group_id=row["source_group_id"],
                anchor_code=row["anchor_code"],
                depth=row["depth"],
                role_code=row["role_code"],
                filler_code=row["filler_code"],
                structural_path=tuple(row["structural_path"]),
                member_position=row["member_position"],
            )
        )
    return by_code


def _dispositions_by_code(
    rows: Sequence[RowMapping],
) -> dict[str, list[OccurrenceDisposition]]:
    by_code: dict[str, list[OccurrenceDisposition]] = {}
    for row in rows:
        by_code.setdefault(row["concept_code"], []).append(
            OccurrenceDisposition(
                kind=row["disposition"],
                source_occurrence_id=row["occurrence_id"],
                source_fact_id=row["source_fact_id"],
                normalized_axis=row["normalized_axis"],
                source_filler=row["source_filler"],
                retained_filler=row["retained_filler"],
                semantic_route=row["semantic_route"],
                semantic_type=row["semantic_type"],
                r82_part=row["r82_part"],
                r82_whole=row["r82_whole"],
                specificity_path=tuple(
                    SpecificityPathEdge(**item)
                    for item in (
                        _json.loads(row["specificity_path"])
                        if isinstance(row["specificity_path"], str)
                        else row["specificity_path"]
                    )
                ),
                policy_decision_identity=row["policy_decision_identity"],
            )
        )
    return by_code


def _comparator_run_from_row(
    run_id: str, row: RowMapping, worklist: Sequence[str]
) -> ComparatorRun:
    from ontolib.decomposition.r101_comparator import (  # noqa: PLC0415
        ComparatorRun,
    )

    if row["status"] != "complete" or row["publication_state"] != "published":
        raise RunStateError(
            f"decomposition run {run_id!r} is not complete and published"
        )
    representation_identity = row["representation_identity"]
    artifact_path = row["publication_artifact_path"]
    if representation_identity is None or artifact_path is None:
        raise RunStateError(f"decomposition run {run_id!r} lacks publication evidence")
    try:
        run = ComparatorRun.model_validate_json(
            _json.dumps(
                {
                    "run_id": run_id,
                    "ncit_version": row["ncit_version"],
                    "fingerprint": row["fingerprint"],
                    "fingerprint_identity": row["fingerprint_sha256"],
                    "worklist": tuple(worklist),
                    "representation_identity": representation_identity,
                    "publication_artifact_path": artifact_path,
                },
                sort_keys=True,
            )
        )
    except ValidationError as exc:
        raise RunIdentityMismatchError(
            "persisted comparator evidence violates its source schema"
        ) from exc
    if row["source_identity"] != run.fingerprint.source_identity:
        raise RunIdentityMismatchError(
            "persisted run source identity does not match its fingerprint"
        )
    return run


class ProvenanceStore:
    """Persistence for decomposition run manifests and constituents."""

    def __init__(self, sf: async_sessionmaker[AsyncSession]) -> None:
        self._sf = sf

    @asynccontextmanager
    async def publication_lock(self) -> AsyncIterator[None]:
        """Serialize publishers while keeping the database connection checked out."""
        engine = self._sf.kw.get("bind")
        if not isinstance(engine, AsyncEngine):
            raise TypeError(
                "decomposition session factory must be bound to an AsyncEngine"
            )
        async with engine.connect() as connection:
            lock_acquired = False
            try:
                await _acquire_publication_lock(connection)
                lock_acquired = True
                yield
            except BaseException as original:
                if lock_acquired:
                    try:
                        await _release_publication_lock(connection)
                    except BaseException as unlock_error:
                        original.add_note(
                            "Failed to release decomposition publication lock: "
                            f"{unlock_error}"
                        )
                        await _invalidate_without_masking(connection, original)
                raise
            else:
                try:
                    await _release_publication_lock(connection)
                except BaseException as unlock_error:
                    await _invalidate_without_masking(connection, unlock_error)
                    raise

    async def create_run(
        self,
        run_id: str,
        ncit_version: str,
        fingerprint: RunFingerprint,
    ) -> None:
        """Atomically create one immutable run and its exact ordered worklist."""
        async with self._sf() as session, session.begin():
            await self._insert_run(
                session, run_id, ncit_version, fingerprint, execution_identity=None
            )

    @staticmethod
    async def _insert_run(
        session: AsyncSession,
        run_id: str,
        ncit_version: str,
        fingerprint: RunFingerprint,
        *,
        execution_identity: str | None,
    ) -> None:
        now = datetime.datetime.now(datetime.UTC)
        await session.execute(
            text(
                "INSERT INTO decomp_run "
                "(id, branch, status, ncit_version, started_at, "
                "source_identity, fingerprint, fingerprint_sha256, emitted_at, "
                "publication_state, execution_identity) "
                "VALUES (:id, :branch, 'running', :ncit_version, :started_at, "
                ":source_identity, CAST(:fingerprint AS jsonb), "
                ":fingerprint_sha256, :emitted_at, :publication_state, "
                ":execution_identity)"
            ),
            {
                "id": run_id,
                "branch": fingerprint.branch,
                "ncit_version": ncit_version,
                "started_at": now,
                "source_identity": fingerprint.source_identity,
                "fingerprint": fingerprint.model_dump_json(),
                "fingerprint_sha256": fingerprint.identity,
                "emitted_at": fingerprint.emitted_at,
                "publication_state": (
                    "not_requested" if fingerprint.output_mode == "none" else "pending"
                ),
                "execution_identity": execution_identity,
            },
        )
        if fingerprint.worklist:
            await session.execute(
                text(
                    "INSERT INTO decomp_work_item "
                    "(run_id, concept_code, ordinal) "
                    "VALUES (:run_id, :concept_code, :ordinal)"
                ),
                [
                    {
                        "run_id": run_id,
                        "concept_code": code,
                        "ordinal": ordinal,
                    }
                    for ordinal, code in enumerate(fingerprint.worklist)
                ],
            )
        await session.execute(
            text(
                "INSERT INTO decomp_run_stage (run_id,stage,ordinal) "
                "VALUES (:run_id,:stage,:ordinal)"
            ),
            [
                {"run_id": run_id, "stage": stage, "ordinal": ordinal}
                for ordinal, stage in enumerate(RUN_STAGE_SEQUENCE)
            ],
        )

    async def admit_run(
        self,
        run_id: str,
        ncit_version: str,
        fingerprint: RunFingerprint,
        execution: FullRunExecutionIdentity,
        *,
        resume_run_id: str | None = None,
    ) -> RunAdmission:
        """Validate or create one exact run under the database uniqueness authority."""
        if FullRunExecutionIdentity.from_fingerprint(fingerprint) != execution:
            return Refused(reason=RefusalReason.IDENTITY_MISMATCH)
        try:
            async with self._sf() as session, session.begin():
                rows = await self._admission_candidates(
                    session,
                    resume_run_id=resume_run_id,
                    execution_identity=execution.identity,
                )
                if resume_run_id is None and len(rows) > 1:
                    return Refused(reason=RefusalReason.AMBIGUOUS_COMPATIBLE_RUNS)
                if rows:
                    return await self._admit_existing(
                        session,
                        rows[0],
                        execution,
                        explicit_resume=resume_run_id is not None,
                    )
                if resume_run_id is not None:
                    return Refused(reason=RefusalReason.IDENTITY_MISMATCH)
                await self._insert_run(
                    session,
                    run_id,
                    ncit_version,
                    fingerprint,
                    execution_identity=execution.identity,
                )
                return FreshAdmitted(run_id=run_id)
        except IntegrityError:
            return Refused(reason=RefusalReason.ACTIVE_RUN_EXISTS)

    @staticmethod
    async def _admission_candidates(
        session: AsyncSession,
        *,
        resume_run_id: str | None,
        execution_identity: str,
    ) -> Sequence[RowMapping]:
        query = (
            "SELECT id,status,publication_state,source_identity,fingerprint,"
            "fingerprint_sha256,execution_identity FROM decomp_run "
            "WHERE id=:candidate FOR UPDATE"
            if resume_run_id is not None
            else "SELECT id,status,publication_state,source_identity,fingerprint,"
            "fingerprint_sha256,execution_identity FROM decomp_run "
            "WHERE execution_identity=:candidate ORDER BY started_at FOR UPDATE"
        )
        candidate = resume_run_id or execution_identity
        return (
            (await session.execute(text(query), {"candidate": candidate}))
            .mappings()
            .all()
        )

    async def _admit_existing(
        self,
        session: AsyncSession,
        row: RowMapping,
        execution: FullRunExecutionIdentity,
        *,
        explicit_resume: bool,
    ) -> RunAdmission:
        run_id = cast("str", row["id"])
        if row["source_identity"] != execution.source_identity:
            return Refused(reason=RefusalReason.SOURCE_DRIFT)
        if row["execution_identity"] != execution.identity:
            return Refused(reason=RefusalReason.IDENTITY_MISMATCH)
        fingerprint, refusal = await self._validate_admission_artifacts(
            session, row, execution, run_id
        )
        if refusal is not None or fingerprint is None:
            return Refused(reason=refusal or RefusalReason.IDENTITY_MISMATCH)
        return await self._admission_for_state(
            session,
            run_id,
            cast("str", row["status"]),
            cast("str", row["publication_state"]),
            fingerprint,
            explicit_resume=explicit_resume,
        )

    async def _validate_admission_artifacts(
        self,
        session: AsyncSession,
        row: RowMapping,
        execution: FullRunExecutionIdentity,
        run_id: str,
    ) -> tuple[RunFingerprint | None, RefusalReason | None]:
        try:
            fingerprint = self._validated_fingerprint(
                row["fingerprint"], row["fingerprint_sha256"]
            )
        except RunIdentityMismatchError, ValidationError:
            return None, RefusalReason.IDENTITY_MISMATCH
        if FullRunExecutionIdentity.from_fingerprint(fingerprint) != execution:
            return None, RefusalReason.IDENTITY_MISMATCH
        try:
            await self._require_materialized_worklist(session, run_id, fingerprint)
        except RunIdentityMismatchError:
            return None, RefusalReason.IDENTITY_MISMATCH
        stages = (
            (
                await session.execute(
                    text(
                        "SELECT stage,ordinal FROM decomp_run_stage "
                        "WHERE run_id=:run_id ORDER BY ordinal"
                    ),
                    {"run_id": run_id},
                )
            )
            .mappings()
            .all()
        )
        try:
            _require_stage_inventory(stages)
        except RunIdentityMismatchError:
            return None, RefusalReason.STAGE_SCHEMA_MISMATCH
        return fingerprint, None

    async def _admission_for_state(
        self,
        session: AsyncSession,
        run_id: str,
        status: str,
        publication_state: str,
        fingerprint: RunFingerprint,
        *,
        explicit_resume: bool,
    ) -> RunAdmission:
        kind = _ADMISSION_STATE_KIND.get((status, publication_state))
        if kind is None:
            return Refused(reason=RefusalReason.IDENTITY_MISMATCH)
        if kind == "complete":
            return Refused(reason=RefusalReason.COMPLETED_RUN_EXISTS)
        if not explicit_resume:
            return Refused(reason=_existing_run_refusal(kind))
        await _reopen_run(session, run_id)
        return ResumeAdmitted(
            run_id=run_id,
            resume_kind=(
                ResumeKind.PUBLICATION if kind == "publication" else ResumeKind.SEMANTIC
            ),
        )

    async def run_stages(self, run_id: str) -> tuple[RunStageCheckpoint, ...]:
        """Read the exact stage inventory; legacy/missing inventories fail closed."""
        async with self._sf() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT run_id,stage,ordinal,state,attempt_count,"
                            "claim_token,"
                            "input_identity,output_identity,output_payload,started_at,"
                            "finished_at,failed_at,error_type,error_message FROM "
                            "decomp_run_stage WHERE run_id=:run_id ORDER BY ordinal"
                        ),
                        {"run_id": run_id},
                    )
                )
                .mappings()
                .all()
            )
        if tuple(row["stage"] for row in rows) != RUN_STAGE_SEQUENCE:
            raise RunIdentityMismatchError(
                "persisted run has no complete stage checkpoint inventory"
            )
        return tuple(RunStageCheckpoint.model_validate(dict(row)) for row in rows)

    async def fingerprint_for_run(self, run_id: str) -> RunFingerprint:
        """Read a fingerprint only after its persisted digest has been verified."""
        async with self._sf() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT fingerprint,fingerprint_sha256 FROM decomp_run "
                            "WHERE id=:run_id"
                        ),
                        {"run_id": run_id},
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise RunStateError(f"decomposition run {run_id!r} does not exist")
        return self._validated_fingerprint(
            row["fingerprint"], row["fingerprint_sha256"]
        )

    async def claim_stage(
        self, run_id: str, stage: RunStageName, input_identity: str
    ) -> UUID | None:
        """Claim the first incomplete stage using an immutable input identity."""
        if len(input_identity) != _SHA256_HEX_LENGTH:
            raise RunIdentityMismatchError("stage input identity must be SHA-256")
        token = uuid4()
        now = datetime.datetime.now(datetime.UTC)
        ordinal = RUN_STAGE_SEQUENCE.index(stage)
        async with self._sf() as session, session.begin():
            run = await session.execute(
                text("SELECT status FROM decomp_run WHERE id=:run_id FOR UPDATE"),
                {"run_id": run_id},
            )
            if run.scalar_one_or_none() != "running":
                raise RunStateError("stage claim requires a running decomposition run")
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT stage,ordinal,state,input_identity FROM "
                            "decomp_run_stage "
                            "WHERE run_id=:run_id ORDER BY ordinal FOR UPDATE"
                        ),
                        {"run_id": run_id},
                    )
                )
                .mappings()
                .all()
            )
            _require_stage_inventory(rows)
            if any(row["state"] != "complete" for row in rows[:ordinal]):
                raise RunStateError("upstream stage is not complete")
            target = rows[ordinal]
            if _completed_stage_matches(target, input_identity):
                return None
            if target["input_identity"] not in {None, input_identity}:
                raise RunIdentityMismatchError("stage input identity does not match")
            await session.execute(
                text(
                    "UPDATE decomp_run_stage SET state='running',attempt_count="
                    "attempt_count+1,claim_token=:token,input_identity=:input_identity,"
                    "started_at=:started_at,finished_at=NULL,failed_at=NULL,error_type=NULL,"
                    "error_message=NULL WHERE run_id=:run_id AND stage=:stage"
                ),
                {
                    "run_id": run_id,
                    "stage": stage,
                    "token": token,
                    "input_identity": input_identity,
                    "started_at": now,
                },
            )
        return token

    async def complete_stage(
        self,
        run_id: str,
        stage: RunStageName,
        claim_token: UUID,
        output_payload: dict[str, object],
    ) -> str:
        """Atomically seal a stage output under its fencing token."""
        output_identity = stage_output_identity(output_payload)
        async with self._sf() as session, session.begin():
            result = await session.execute(
                text(
                    "UPDATE decomp_run_stage SET state='complete',claim_token=NULL,"
                    "output_identity=:output_identity,"
                    "output_payload=CAST(:payload AS jsonb),"
                    "finished_at=:finished_at WHERE run_id=:run_id AND stage=:stage "
                    "AND state='running' AND claim_token=:claim_token"
                ),
                {
                    "run_id": run_id,
                    "stage": stage,
                    "claim_token": claim_token,
                    "output_identity": output_identity,
                    "payload": _json.dumps(output_payload, sort_keys=True),
                    "finished_at": datetime.datetime.now(datetime.UTC),
                },
            )
            if not cast("int", result.rowcount):  # type: ignore[attr-defined]
                raise RunStateError("stage claim changed before completion")
        return output_identity

    async def fail_stage(
        self,
        run_id: str,
        stage: RunStageName,
        claim_token: UUID,
        error: BaseException,
    ) -> None:
        """Record a bounded stage failure without changing completed upstream stages."""
        error_type, error_message = _bounded_failure(error)
        async with self._sf() as session, session.begin():
            result = await session.execute(
                text(
                    "UPDATE decomp_run_stage SET state='failed',claim_token=NULL,"
                    "failed_at=:failed_at,error_type=:error_type,"
                    "error_message=:error_message "
                    "WHERE run_id=:run_id AND stage=:stage AND state='running' "
                    "AND claim_token=:claim_token"
                ),
                {
                    "run_id": run_id,
                    "stage": stage,
                    "claim_token": claim_token,
                    "failed_at": datetime.datetime.now(datetime.UTC),
                    "error_type": error_type,
                    "error_message": error_message,
                },
            )
            if not cast("int", result.rowcount):  # type: ignore[attr-defined]
                raise RunStateError("stage claim changed before failure record")

    async def initialize_residual_fillers(
        self,
        run_id: str,
        filler_codes: Sequence[str],
        *,
        source_identity: str,
        detector_identity: str,
    ) -> None:
        """Create or validate one exact sorted filler workset."""
        codes = tuple(sorted(set(filler_codes)))
        if len(codes) != len(filler_codes):
            raise RunIdentityMismatchError(
                "residual filler inventory contains duplicates"
            )
        async with self._sf() as session, session.begin():
            run = (
                await session.execute(
                    text(
                        "SELECT source_identity FROM decomp_run WHERE id=:run_id "
                        "FOR UPDATE"
                    ),
                    {"run_id": run_id},
                )
            ).scalar_one_or_none()
            stage = (
                await session.execute(
                    text(
                        "SELECT state FROM decomp_run_stage WHERE run_id=:run_id "
                        "AND stage='residual-classification' FOR UPDATE"
                    ),
                    {"run_id": run_id},
                )
            ).scalar_one_or_none()
            _require_residual_context(run, source_identity, stage)
            existing = (
                (
                    await session.execute(
                        text(
                            "SELECT filler_code,source_identity,detector_identity FROM "
                            "decomp_residual_filler WHERE run_id=:run_id "
                            "ORDER BY ordinal"
                        ),
                        {"run_id": run_id},
                    )
                )
                .mappings()
                .all()
            )
            expected = tuple(
                (code, source_identity, detector_identity) for code in codes
            )
            if _existing_residual_inventory_matches(existing, expected):
                return
            if codes:
                await session.execute(
                    text(
                        "INSERT INTO decomp_residual_filler "
                        "(run_id,filler_code,ordinal,source_identity,"
                        "detector_identity) "
                        "VALUES (:run_id,:filler_code,:ordinal,:source_identity,"
                        ":detector_identity)"
                    ),
                    [
                        {
                            "run_id": run_id,
                            "filler_code": code,
                            "ordinal": ordinal,
                            "source_identity": source_identity,
                            "detector_identity": detector_identity,
                        }
                        for ordinal, code in enumerate(codes)
                    ],
                )

    async def pending_residual_fillers(self, run_id: str) -> list[str]:
        async with self._sf() as session:
            result = await session.execute(
                text(
                    "SELECT filler_code FROM decomp_residual_filler "
                    "WHERE run_id=:run_id "
                    "AND state<>'complete' ORDER BY ordinal"
                ),
                {"run_id": run_id},
            )
            return list(result.scalars().all())

    async def claim_residual_filler(self, run_id: str, filler_code: str) -> UUID | None:
        token = uuid4()
        async with self._sf() as session, session.begin():
            result = await session.execute(
                text(
                    "UPDATE decomp_residual_filler SET state='running',attempt_count="
                    "attempt_count+1,claim_token=:token,claimed_at=:claimed_at,"
                    "failed_at=NULL,error_type=NULL,error_message=NULL "
                    "WHERE run_id=:run_id AND filler_code=:filler_code "
                    "AND state IN ('pending','running','failed') "
                    "RETURNING claim_token"
                ),
                {
                    "run_id": run_id,
                    "filler_code": filler_code,
                    "token": token,
                    "claimed_at": datetime.datetime.now(datetime.UTC),
                },
            )
            claimed = result.scalar_one_or_none()
            return UUID(str(claimed)) if claimed is not None else None

    async def complete_residual_filler(
        self,
        run_id: str,
        filler_code: str,
        claim_token: UUID,
        *,
        definition_identity: str,
        classification: str,
        unsupported_reason: str | None,
    ) -> None:
        async with self._sf() as session, session.begin():
            result = await session.execute(
                text(
                    "UPDATE decomp_residual_filler SET state='complete',"
                    "claim_token=NULL,"
                    "claimed_at=NULL,definition_identity=:definition_identity,"
                    "classification=:classification,unsupported_reason=:unsupported_reason,"
                    "completed_at=:completed_at WHERE run_id=:run_id AND "
                    "filler_code=:filler_code AND state='running' AND "
                    "claim_token=:claim_token"
                ),
                {
                    "run_id": run_id,
                    "filler_code": filler_code,
                    "claim_token": claim_token,
                    "definition_identity": definition_identity,
                    "classification": classification,
                    "unsupported_reason": unsupported_reason,
                    "completed_at": datetime.datetime.now(datetime.UTC),
                },
            )
            if not cast("int", result.rowcount):  # type: ignore[attr-defined]
                raise RunStateError("residual filler claim changed before completion")

    async def fail_residual_filler(
        self,
        run_id: str,
        filler_code: str,
        claim_token: UUID,
        error: BaseException,
    ) -> None:
        error_type, error_message = _bounded_failure(error)
        async with self._sf() as session, session.begin():
            result = await session.execute(
                text(
                    "UPDATE decomp_residual_filler SET state='failed',claim_token=NULL,"
                    "claimed_at=NULL,failed_at=:failed_at,error_type=:error_type,"
                    "error_message=:error_message WHERE run_id=:run_id AND "
                    "filler_code=:filler_code AND state='running' AND "
                    "claim_token=:claim_token"
                ),
                {
                    "run_id": run_id,
                    "filler_code": filler_code,
                    "claim_token": claim_token,
                    "failed_at": datetime.datetime.now(datetime.UTC),
                    "error_type": error_type,
                    "error_message": error_message,
                },
            )
            if not cast("int", result.rowcount):  # type: ignore[attr-defined]
                raise RunStateError(
                    "residual filler claim changed before failure record"
                )

    async def residual_filler_classifications(
        self, run_id: str
    ) -> tuple[ResidualFillerClassification, ...]:
        async with self._sf() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT run_id,filler_code,ordinal,source_identity,"
                            "definition_identity,detector_identity,classification,"
                            "unsupported_reason FROM decomp_residual_filler WHERE "
                            "run_id=:run_id AND state='complete' ORDER BY ordinal"
                        ),
                        {"run_id": run_id},
                    )
                )
                .mappings()
                .all()
            )
        return tuple(
            ResidualFillerClassification.model_validate(dict(row)) for row in rows
        )

    @staticmethod
    def _validated_fingerprint(
        raw: object,
        persisted_identity: str,
    ) -> RunFingerprint:
        try:
            fingerprint = RunFingerprint.model_validate_json(
                _json.dumps(raw, sort_keys=True)
            )
        except ValidationError as exc:
            # Migration 0008 stamps every pre-exact-run row with a schema_version 0
            # fingerprint and a zero identity, and demotes any non-complete run to
            # 'failed'. Distinguish that expected shape from a fingerprint that is
            # corrupt or was modified outside the pipeline: reporting the latter as a
            # benign migration artifact would send an operator to close the ticket.
            raise RunIdentityMismatchError(
                "persisted run fingerprint "
                f"{_invalid_fingerprint_detail(raw, persisted_identity)}"
            ) from exc
        if fingerprint.identity != persisted_identity:
            raise RunIdentityMismatchError(
                "persisted run fingerprint does not match its SHA-256 identity"
            )
        return fingerprint

    @staticmethod
    async def _require_materialized_worklist(
        session: AsyncSession,
        run_id: str,
        fingerprint: RunFingerprint,
    ) -> None:
        result = await session.execute(
            text(
                "SELECT concept_code FROM decomp_work_item "
                "WHERE run_id = :run_id ORDER BY ordinal"
            ),
            {"run_id": run_id},
        )
        if tuple(result.scalars().all()) != fingerprint.worklist:
            raise RunIdentityMismatchError(
                "materialized worklist does not match the immutable run fingerprint"
            )

    @staticmethod
    def require_resume_identity(
        fingerprint: RunFingerprint,
        expected: RunResumeIdentity,
        run_id: str,
    ) -> None:
        """Apply the production caller-controlled resume identity contract."""
        actual = RunResumeIdentity.from_fingerprint(fingerprint)
        if actual == expected:
            return
        dimension = (
            "source identity"
            if actual.source_identity != expected.source_identity
            else "configuration"
        )
        raise RunIdentityMismatchError(
            f"resume {dimension} does not match persisted run {run_id!r}"
        )

    async def resume_run(
        self,
        run_id: str,
        expected: RunResumeIdentity,
    ) -> RunFingerprint:
        """Validate and reopen only a matching running/failed exact run."""
        async with self._sf() as session, session.begin():
            result = await session.execute(
                text(
                    "SELECT status, fingerprint, fingerprint_sha256 "
                    "FROM decomp_run WHERE id = :id FOR UPDATE"
                ),
                {"id": run_id},
            )
            row = result.mappings().first()
            if row is None:
                raise RunStateError(f"decomposition run {run_id!r} does not exist")
            if row["status"] not in {"running", "failed"}:
                raise RunStateError(
                    f"decomposition run {run_id!r} is {row['status']!r}, "
                    "not running or failed"
                )
            fingerprint = self._validated_fingerprint(
                row["fingerprint"], row["fingerprint_sha256"]
            )
            if fingerprint.rehearsal_nonce is not None:
                raise RunStateError(
                    f"decomposition run {run_id!r} is a rehearsal; rehearsals are "
                    "throwaway runs and cannot be resumed"
                )
            await self._require_materialized_worklist(session, run_id, fingerprint)
            self.require_resume_identity(fingerprint, expected, run_id)
            await _reopen_run(session, run_id)
            return fingerprint

    async def pending_codes(self, run_id: str) -> list[str]:
        """Exact non-complete worklist in its original deterministic order."""
        async with self._sf() as session:
            result = await session.execute(
                text(
                    "SELECT concept_code FROM decomp_work_item "
                    "WHERE run_id = :run_id AND state <> 'complete' "
                    "ORDER BY ordinal"
                ),
                {"run_id": run_id},
            )
            return list(result.scalars().all())

    async def unknown_outcome_codes(self, run_id: str) -> tuple[str, ...]:
        """Return every explicitly typed unknown concept in worklist order."""
        async with self._sf() as session:
            result = await session.execute(
                text(
                    "SELECT concept_code FROM decomp_work_item WHERE run_id=:run_id "
                    "AND state='complete' AND outcome='unknown' ORDER BY ordinal"
                ),
                {"run_id": run_id},
            )
            return tuple(result.scalars().all())

    async def claim_work_item(self, run_id: str, concept_code: str) -> UUID | None:
        """Atomically claim one pending/failed item; return its fencing token."""
        token = uuid4()
        async with self._sf() as session, session.begin():
            result = await session.execute(
                text(
                    "UPDATE decomp_work_item SET state = 'running', "
                    "attempt_count = attempt_count + 1, claim_token = :token, "
                    "claimed_at = :claimed_at, error_type = NULL, "
                    "error_message = NULL, failed_at = NULL "
                    "WHERE run_id = :run_id AND concept_code = :concept_code "
                    "AND state IN ('pending', 'failed') "
                    "AND EXISTS (SELECT 1 FROM decomp_run "
                    "WHERE id = :run_id AND status = 'running') "
                    "RETURNING claim_token"
                ),
                {
                    "run_id": run_id,
                    "concept_code": concept_code,
                    "token": token,
                    "claimed_at": datetime.datetime.now(datetime.UTC),
                },
            )
            claimed = result.scalar()
            return UUID(str(claimed)) if claimed is not None else None

    async def complete_work_item(
        self,
        run_id: str,
        concept_code: str,
        claim_token: UUID,
        *,
        decomposition: Decomposition | None,
        minted: tuple[MintedProposal, ...],
        semantic_types: tuple[str, ...],
        outcome: ConceptOutcome | None = None,
    ) -> None:
        """Replace one concept's rows and mark it complete in one transaction."""
        constituents, is_decomposed, is_residual = _completion_outcome(
            concept_code, decomposition, minted
        )
        outcome, canonical_semantic_types, complete_definition = (
            _validated_completion_metadata(
                decomposition,
                outcome,
                semantic_types,
                is_decomposed=is_decomposed,
                is_residual=is_residual,
            )
        )
        async with self._sf() as session, session.begin():
            locked = await session.execute(
                text(
                    "SELECT w.state, w.claim_token, r.status "
                    "FROM decomp_work_item w JOIN decomp_run r ON r.id = w.run_id "
                    "WHERE w.run_id = :run_id AND w.concept_code = :concept_code "
                    "FOR UPDATE OF w"
                ),
                {"run_id": run_id, "concept_code": concept_code},
            )
            row = locked.mappings().first()
            _require_owned_claim(row, run_id, concept_code, claim_token)
            await _delete_completion_rows(session, run_id, concept_code)
            await _persist_completion_rows(
                session,
                run_id,
                concept_code,
                constituents,
                complete_definition,
                decomposition.occurrence_dispositions
                if decomposition is not None
                else (),
                minted,
            )
            await _mark_work_item_complete(
                session,
                run_id,
                concept_code,
                claim_token,
                decomposition,
                outcome,
                canonical_semantic_types,
                constituents,
                minted,
                is_decomposed=is_decomposed,
                is_residual=is_residual,
            )

    async def fail_work_item(
        self,
        run_id: str,
        concept_code: str,
        claim_token: UUID,
        error: BaseException,
    ) -> None:
        """Record bounded item failure and demote the enclosing run, in one
        transaction.

        Runs after the processing transaction has rolled back. Because the run leaves
        ``running``, no further work item can be claimed and ``finish_run`` refuses
        until the run is resumed.
        """
        error_type, error_message = _bounded_failure(error)
        failed_at = datetime.datetime.now(datetime.UTC)
        async with self._sf() as session, session.begin():
            result = await session.execute(
                text(
                    "UPDATE decomp_work_item SET state = 'failed', "
                    "claim_token = NULL, claimed_at = NULL, "
                    "error_type = :error_type, error_message = :error_message, "
                    "failed_at = :failed_at "
                    "WHERE run_id = :run_id AND concept_code = :concept_code "
                    "AND state = 'running' AND claim_token = :claim_token"
                ),
                {
                    "run_id": run_id,
                    "concept_code": concept_code,
                    "claim_token": claim_token,
                    "error_type": error_type,
                    "error_message": error_message,
                    "failed_at": failed_at,
                },
            )
            if not cast("int", result.rowcount):  # type: ignore[attr-defined]
                raise RunStateError("work-item claim changed before failure record")
            await session.execute(
                text(
                    "UPDATE decomp_run SET status = 'failed', "
                    "error_type = :error_type, error_message = :error_message "
                    "WHERE id = :run_id AND status = 'running'"
                ),
                {
                    "run_id": run_id,
                    "error_type": error_type,
                    "error_message": error_message,
                },
            )

    async def fail_run(self, run_id: str, error: BaseException) -> bool:
        """Leave a source-bound run visibly failed with bounded metadata.

        Returns whether the run is recorded as failed once this call returns, not
        whether this call performed the write: ``fail_work_item`` already demotes the
        enclosing run, so an ordinary work-item failure reaches here with the run
        already ``failed`` and correctly recorded. ``False`` therefore means no
        failure is recorded — the run holds a different terminal state, or its row is
        gone.
        """
        error_type, error_message = _bounded_failure(error)
        async with self._sf() as session, session.begin():
            result = await session.execute(
                text(
                    "UPDATE decomp_run SET status = 'failed', "
                    "finished_at = NULL, error_type = :error_type, "
                    "error_message = :error_message "
                    "WHERE id = :run_id AND status = 'running'"
                ),
                {
                    "run_id": run_id,
                    "error_type": error_type,
                    "error_message": error_message,
                },
            )
            if cast("int", result.rowcount):  # type: ignore[attr-defined]
                return True
            current = await session.execute(
                text("SELECT status FROM decomp_run WHERE id = :run_id"),
                {"run_id": run_id},
            )
            # fail_work_item already demotes the enclosing run, so an ordinary
            # work-item failure lands here with the failure correctly recorded.
            return current.scalar() == "failed"

    async def invalidate_run(self, run_id: str, error: BaseException) -> bool:
        """Discard every persisted result after a source-identity violation.

        One PostgreSQL transaction, so the rows cannot be partially discarded.
        Returns ``False`` without discarding anything when the run is no longer
        ``running``; the caller must surface that, because the results then survive.
        Files already written outside PostgreSQL are not covered here.
        """
        error_type, error_message = _bounded_failure(error)
        failed_at = datetime.datetime.now(datetime.UTC)
        async with self._sf() as session, session.begin():
            locked = await session.execute(
                text("SELECT status FROM decomp_run WHERE id = :run_id FOR UPDATE"),
                {"run_id": run_id},
            )
            if locked.scalar() != "running":
                return False
            for statement in (
                "DELETE FROM decomp_definition_fact WHERE run_id = :run_id",
                "DELETE FROM decomp_definition_group_edge WHERE run_id = :run_id",
                "DELETE FROM decomp_definition_group WHERE run_id = :run_id",
                "DELETE FROM decomp_constituent WHERE run_id = :run_id",
                "DELETE FROM decomp_minted_proposal WHERE run_id = :run_id",
            ):
                await session.execute(text(statement), {"run_id": run_id})
            await session.execute(
                text(
                    "UPDATE decomp_work_item SET state = 'failed', "
                    "claim_token = NULL, claimed_at = NULL, semantic_type = NULL, "
                    "semantic_types = NULL, outcome = NULL, "
                    "is_decomposed = NULL, is_residual = NULL, "
                    "has_complete_definition = false, "
                    "constituent_count = NULL, minted_count = NULL, "
                    "error_type = :error_type, error_message = :error_message, "
                    "failed_at = :failed_at, completed_at = NULL "
                    "WHERE run_id = :run_id AND attempt_count > 0"
                ),
                {
                    "run_id": run_id,
                    "error_type": error_type,
                    "error_message": error_message,
                    "failed_at": failed_at,
                },
            )
            result = await session.execute(
                text(
                    "UPDATE decomp_run SET status = 'failed', finished_at = NULL, "
                    "metrics = NULL, error_type = :error_type, "
                    "error_message = :error_message WHERE id = :run_id "
                    "AND status = 'running'"
                ),
                {
                    "run_id": run_id,
                    "error_type": error_type,
                    "error_message": error_message,
                },
            )
            return bool(cast("int", result.rowcount))  # type: ignore[attr-defined]

    async def decompositions_for_run(self, run_id: str) -> list[Decomposition]:
        """Reconstruct the normalized artifact in persisted worklist order."""
        async with self._sf() as session:
            await _require_persisted_completion_counts(session, run_id)
            (
                work_item_rows,
                constituent_rows,
                definition_rows,
                group_rows,
                edge_rows,
                occurrence_rows,
                occurrence_link_rows,
                disposition_rows,
            ) = await _load_decomposition_rows(session, run_id)

        constituents_by_code = _constituents_by_code(
            constituent_rows, occurrence_link_rows
        )
        facts_by_code = _definition_facts_by_code(definition_rows)
        groups_by_code, roots_by_code = _definition_groups_by_code(
            group_rows,
            edge_rows,
        )
        occurrences_by_code = _occurrences_by_code(occurrence_rows)
        dispositions_by_code = _dispositions_by_code(disposition_rows)
        return [
            Decomposition(
                code=row["concept_code"],
                semantic_type=row["semantic_type"],
                constituents=tuple(constituents_by_code.get(row["concept_code"], [])),
                complete_definition=_complete_definition_for_code(
                    row["concept_code"],
                    row["has_complete_definition"],
                    facts_by_code,
                    groups_by_code,
                    roots_by_code,
                    occurrences_by_code,
                ),
                occurrence_dispositions=tuple(
                    dispositions_by_code.get(row["concept_code"], [])
                ),
            )
            for row in work_item_rows
        ]

    async def completed_run_for_evidence(self, run_id: str) -> CompletedRunForEvidence:
        """Return only a completed, published run with validated immutable identity."""
        async with self._sf() as session:
            result = await session.execute(
                text(
                    "SELECT status, ncit_version, source_identity, fingerprint, "
                    "fingerprint_sha256, publication_state, "
                    "representation_identity, publication_artifact_path "
                    "FROM decomp_run WHERE id = :run_id"
                ),
                {"run_id": run_id},
            )
            row = result.mappings().first()
            if row is None:
                raise RunStateError(f"decomposition run {run_id!r} does not exist")
            if row["status"] != "complete" or row["publication_state"] != "published":
                raise RunStateError(
                    f"decomposition run {run_id!r} is not complete and published"
                )
            fingerprint = self._validated_fingerprint(
                row["fingerprint"], row["fingerprint_sha256"]
            )
            await self._require_materialized_worklist(session, run_id, fingerprint)
            if row["source_identity"] != fingerprint.source_identity:
                raise RunIdentityMismatchError(
                    "persisted run source identity does not match its fingerprint"
                )
            representation_identity = row["representation_identity"]
            artifact_path = row["publication_artifact_path"]
            if representation_identity is None or artifact_path is None:
                raise RunStateError(
                    f"decomposition run {run_id!r} lacks publication evidence"
                )
            return CompletedRunForEvidence(
                run_id=run_id,
                ncit_version=row["ncit_version"],
                fingerprint=fingerprint,
                representation_identity=representation_identity,
                publication_artifact_path=artifact_path,
            )

    async def completed_comparator_run_for_evidence(self, run_id: str) -> ComparatorRun:
        """Read completed publication fields for a controlled historical comparison."""
        async with self._sf() as session:
            result = await session.execute(
                text(
                    "SELECT status, ncit_version, source_identity, fingerprint, "
                    "fingerprint_sha256, publication_state, representation_identity, "
                    "publication_artifact_path FROM decomp_run WHERE id = :run_id"
                ),
                {"run_id": run_id},
            )
            row = result.mappings().first()
            if row is None:
                raise RunStateError(f"decomposition run {run_id!r} does not exist")
            worklist_result = await session.execute(
                text(
                    "SELECT concept_code FROM decomp_work_item "
                    "WHERE run_id = :run_id ORDER BY ordinal"
                ),
                {"run_id": run_id},
            )
            return _comparator_run_from_row(
                run_id, row, worklist_result.scalars().all()
            )

    async def historical_mixed_chain_run_for_evidence(
        self, run_id: str
    ) -> HistoricalMixedChainRunBinding:
        """Read the exact completed 2b39 run inputs used by historical replay."""
        from ontolib.decomposition.mixed_chain_inventory import (  # noqa: PLC0415
            HistoricalMixedChainRunBinding,
        )

        async with self._sf() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT status,fingerprint,fingerprint_sha256,"
                            "publication_state FROM decomp_run WHERE id=:run_id"
                        ),
                        {"run_id": run_id},
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise RunStateError(f"decomposition run {run_id!r} does not exist")
            if row["status"] != "complete" or row["publication_state"] != "published":
                raise RunStateError(
                    f"decomposition run {run_id!r} is not complete and published"
                )
            worklist = tuple(
                (
                    await session.execute(
                        text(
                            "SELECT concept_code FROM decomp_work_item "
                            "WHERE run_id=:run_id ORDER BY ordinal"
                        ),
                        {"run_id": run_id},
                    )
                )
                .scalars()
                .all()
            )
        try:
            return HistoricalMixedChainRunBinding.model_validate_json(
                _json.dumps(
                    {
                        "run_id": run_id,
                        "fingerprint": row["fingerprint"],
                        "fingerprint_identity": row["fingerprint_sha256"],
                        "materialized_worklist": worklist,
                    },
                    sort_keys=True,
                )
            )
        except ValidationError as exc:
            raise RunIdentityMismatchError(
                "persisted historical mixed-chain run violates its exact schema"
            ) from exc

    async def outcome_counts(self, run_id: str) -> RunOutcomeCounts:
        """Return cumulative counters over the materialized exact worklist."""
        async with self._sf() as session:
            return await _persisted_outcome_counts(session, run_id)

    async def selector_occurrences_for_codes(
        self, run_id: str, concept_codes: tuple[str, ...]
    ) -> tuple[PersistedSelectorOccurrence, ...]:
        """Load exact persisted routed occurrences for a bounded concept set."""
        from ontolib.decomposition.mixed_chain_inventory import (  # noqa: PLC0415
            PersistedSelectorOccurrence,
        )

        if not concept_codes or len(concept_codes) > _MAX_BOUNDED_SELECTOR_CODES:
            raise ValueError("selector occurrence request must contain 1-100 codes")
        if tuple(sorted(set(concept_codes))) != concept_codes:
            raise ValueError("selector occurrence codes must be canonical and unique")
        async with self._sf() as session:
            result = await session.execute(
                text(
                    "SELECT d.concept_code, d.occurrence_id AS source_occurrence_id, "
                    "d.source_fact_id, o.role_code AS source_role, "
                    "o.anchor_code AS anchoring_genus, d.normalized_axis, "
                    "d.source_filler, d.semantic_route, d.semantic_type, "
                    "d.policy_decision_identity FROM decomp_occurrence_disposition d "
                    "JOIN decomp_source_occurrence o USING "
                    "(run_id, concept_code, occurrence_id) WHERE d.run_id = :run_id "
                    "AND d.concept_code = ANY(CAST(:codes AS text[])) "
                    "ORDER BY d.concept_code, d.normalized_axis, d.occurrence_id"
                ),
                {"run_id": run_id, "codes": list(concept_codes)},
            )
            rows = result.mappings().all()
        return tuple(
            PersistedSelectorOccurrence.model_validate(dict(row)) for row in rows
        )

    async def projection_state_for_codes(
        self, run_id: str, concept_codes: tuple[str, ...]
    ) -> tuple[PersistedProjectionState, ...]:
        """Load complete output and disposition state for 1-100 exact codes."""
        from ontolib.decomposition.mixed_chain_projection import (  # noqa: PLC0415
            PersistedProjectionState,
        )

        if not concept_codes or len(concept_codes) > _MAX_BOUNDED_SELECTOR_CODES:
            raise ValueError("projection state request must contain 1-100 codes")
        if tuple(sorted(set(concept_codes))) != concept_codes:
            raise ValueError("projection state codes must be canonical and unique")
        params = {"run_id": run_id, "codes": list(concept_codes)}
        async with self._sf() as session:
            constituent_result = await session.execute(
                text(
                    "SELECT concept_code, axis, filler_code, axis_source, "
                    "source_roles, most_specific, needs_review, "
                    "axis_ambiguity_group_id, source_group_ids, normalized_group_id, "
                    "normalized_group_label, source_definition_ids "
                    "FROM decomp_constituent WHERE "
                    "run_id = :run_id AND concept_code = ANY(CAST(:codes AS text[])) "
                    "ORDER BY concept_code, axis, filler_code"
                ),
                params,
            )
            link_result = await session.execute(
                text(
                    "SELECT concept_code, axis, filler_code, occurrence_id FROM "
                    "decomp_constituent_occurrence WHERE run_id = :run_id AND "
                    "concept_code = ANY(CAST(:codes AS text[])) ORDER BY "
                    "concept_code, axis, filler_code, occurrence_id"
                ),
                params,
            )
            disposition_result = await session.execute(
                text(
                    "SELECT concept_code, occurrence_id, source_fact_id, disposition, "
                    "normalized_axis, source_filler, retained_filler, semantic_route, "
                    "semantic_type, r82_part, r82_whole, specificity_path, "
                    "policy_decision_identity FROM decomp_occurrence_disposition "
                    "WHERE run_id = :run_id AND concept_code = ANY(CAST(:codes AS "
                    "text[])) ORDER BY concept_code, occurrence_id"
                ),
                params,
            )
        constituents = _constituents_by_code(
            constituent_result.mappings().all(), link_result.mappings().all()
        )
        dispositions = _dispositions_by_code(disposition_result.mappings().all())
        return tuple(
            PersistedProjectionState(
                concept_code=code,
                constituents=tuple(constituents.get(code, ())),
                dispositions=tuple(dispositions.get(code, ())),
            )
            for code in concept_codes
        )

    async def corpus_baseline_aggregate(self, run_id: str) -> CorpusBaselineAggregate:
        """Aggregate the complete baseline payload in one bounded SQL query."""
        async with self._sf() as session:
            result = await session.execute(
                text(
                    "SELECT "
                    "(SELECT count(*) FROM decomp_work_item WHERE run_id = :run_id) "
                    "AS worklist_count, "
                    "(SELECT count(*) FROM decomp_work_item WHERE run_id = :run_id "
                    "AND outcome = 'decomposed') AS decomposed, "
                    "(SELECT count(*) FROM decomp_work_item WHERE run_id = :run_id "
                    "AND outcome = 'residual') AS residual, "
                    "(SELECT count(*) FROM decomp_work_item WHERE run_id = :run_id "
                    "AND outcome = 'semantic-excluded') AS semantic_excluded, "
                    "(SELECT count(*) FROM decomp_work_item WHERE run_id = :run_id "
                    "AND outcome = 'atomic-no-op') AS atomic_noop, "
                    "(SELECT count(*) FROM decomp_work_item WHERE run_id = :run_id "
                    "AND outcome = 'unknown') AS unknown, "
                    "(SELECT COALESCE(array_agg(concept_code ORDER BY ordinal) "
                    "FILTER (WHERE outcome = 'decomposed'), ARRAY[]::text[]) "
                    "FROM decomp_work_item WHERE run_id = :run_id) "
                    "AS decomposed_codes, "
                    "(SELECT count(*) FROM decomp_constituent WHERE run_id = :run_id) "
                    "AS emitted_constituent_pair_count, "
                    "(SELECT count(*) FROM decomp_definition_fact "
                    "WHERE run_id = :run_id) AS complete_semantic_fact_count, "
                    "(SELECT count(*) FROM decomp_source_occurrence "
                    "WHERE run_id = :run_id) AS source_occurrence_count, "
                    "(SELECT count(DISTINCT (concept_code, occurrence_id)) "
                    "FROM decomp_constituent_occurrence WHERE run_id = :run_id) "
                    "AS selected_occurrence_count, "
                    "(SELECT count(*) FROM decomp_minted_proposal "
                    "WHERE run_id = :run_id) AS minted_count"
                ),
                {"run_id": run_id},
            )
            row = dict(result.mappings().one())
            return CorpusBaselineAggregate.model_validate(
                {
                    "worklist_count": row["worklist_count"],
                    "outcome_counts": {
                        name: row[name]
                        for name in (
                            "decomposed",
                            "residual",
                            "semantic_excluded",
                            "atomic_noop",
                            "unknown",
                        )
                    },
                    "decomposed_codes": tuple(row["decomposed_codes"]),
                    "emitted_constituent_pair_count": row[
                        "emitted_constituent_pair_count"
                    ],
                    "complete_semantic_fact_count": row["complete_semantic_fact_count"],
                    "source_occurrence_count": row["source_occurrence_count"],
                    "selected_occurrence_count": row["selected_occurrence_count"],
                    "minted_count": row["minted_count"],
                }
            )

    async def r101_occurrence_ledger(
        self,
        old_run_id: str,
        new_run_id: str,
    ) -> R101LedgerSource:
        """Read both exact occurrence inventories and links in one bounded query."""
        from ontolib.decomposition.r101_conservation import (  # noqa: PLC0415
            NonR101DeltaEvidence,
            NonR101DeltaRow,
            R101ConservationValidationError,
            R101LedgerSource,
            classify_non_r101_delta_rows,
            r101_ledger_query_identity,
            r101_non_r101_delta_query,
            r101_occurrence_ledger_query,
        )

        sql = text(r101_occurrence_ledger_query())
        delta_sql = text(r101_non_r101_delta_query())
        async with self._sf() as session:
            result = await session.execute(
                sql, {"old_run_id": old_run_id, "new_run_id": new_run_id}
            )
            rows = result.mappings().all()
            delta_result = await session.execute(
                delta_sql,
                {"old_run_id": old_run_id, "new_run_id": new_run_id},
            )
            delta_rows = delta_result.mappings().all()
        occurrences = _parse_r101_occurrences(rows)
        parsed_delta_rows = tuple(
            NonR101DeltaRow.model_validate_json(_json.dumps(dict(item), sort_keys=True))
            for item in delta_rows
        )
        if len(parsed_delta_rows) != len(set(parsed_delta_rows)):
            raise R101ConservationValidationError("duplicate non-R101 delta evidence")
        r101_occurrences: dict[str, list[str]] = {}
        for item in occurrences:
            if item.old_links != item.new_links:
                r101_occurrences.setdefault(
                    item.old_occurrence.concept_code, []
                ).append(item.old_occurrence.occurrence_id)
        structural_rows, metadata_deltas, classified_rows = (
            classify_non_r101_delta_rows(
                parsed_delta_rows,
                r101_changed_occurrences={
                    concept: tuple(sorted(set(occurrence_ids)))
                    for concept, occurrence_ids in r101_occurrences.items()
                },
            )
        )
        evidence = NonR101DeltaEvidence(
            old_run_id=old_run_id,
            new_run_id=new_run_id,
            query_identity=r101_ledger_query_identity(),
            rows=structural_rows,
            metadata_deltas=metadata_deltas,
            classified_rows=classified_rows,
            raw_typed_delta_count=len(parsed_delta_rows),
        )
        return R101LedgerSource(
            occurrences=tuple(occurrences), non_r101_delta_evidence=evidence
        )

    async def work_item_outcomes(self, run_id: str) -> list[WorkItemOutcome]:
        """Return the exact ordered per-concept outcomes for a run."""
        async with self._sf() as session:
            result = await session.execute(
                text(
                    "SELECT run_id, concept_code, ordinal, state, outcome, "
                    "semantic_type, semantic_types, is_decomposed, is_residual, "
                    "constituent_count, minted_count "
                    "FROM decomp_work_item WHERE run_id = :run_id ORDER BY ordinal"
                ),
                {"run_id": run_id},
            )
            outcomes: list[WorkItemOutcome] = []
            for raw_row in result.mappings().all():
                row = dict(raw_row)
                if row["semantic_types"] is not None:
                    row["semantic_types"] = tuple(row["semantic_types"])
                outcomes.append(WorkItemOutcome.model_validate(row))
            return outcomes

    async def begin_publication(
        self,
        run_id: str,
        *,
        representation_identity: str,
        artifact_path: str,
        built_at: datetime.datetime,
        predecessor: PublicationMarkerSnapshot | None,
    ) -> None:
        """Persist or retry one immutable publication intent.

        Retrying the same intent advances the attempt counter and clears only the
        publication failure. A different representation, destination, or build time
        is rejected because it cannot safely reconcile against an existing marker.
        """
        async with self._sf() as session, session.begin():
            result = await session.execute(
                text(
                    "SELECT status, publication_state, representation_identity, "
                    "publication_artifact_path, publication_built_at, "
                    "publication_predecessor_captured, publication_predecessor "
                    "FROM decomp_run WHERE id = :id FOR UPDATE"
                ),
                {"id": run_id},
            )
            row = result.mappings().first()
            if row is None:
                raise RunStateError(f"decomposition run {run_id!r} does not exist")
            if row["status"] != "running":
                raise RunStateError(f"decomposition run {run_id!r} is not running")
            incomplete = await session.execute(
                text(
                    "SELECT count(*) FROM decomp_work_item "
                    "WHERE run_id = :id AND state <> 'complete'"
                ),
                {"id": run_id},
            )
            if incomplete.scalar_one() != 0:
                raise RunStateError(
                    f"decomposition run {run_id!r} has unfinished work items"
                )
            state = row["publication_state"]
            if state not in {"pending", "publishing", "failed"}:
                raise RunStateError(
                    f"decomposition run {run_id!r} publication is {state!r}"
                )
            requested_identity = (
                representation_identity,
                artifact_path,
                built_at,
            )
            requested_predecessor = (
                predecessor.model_dump(mode="json") if predecessor is not None else None
            )
            _validate_publication_retry(
                row,
                state=state,
                requested_identity=requested_identity,
                requested_predecessor=requested_predecessor,
            )
            await session.execute(
                text(
                    "UPDATE decomp_run SET publication_state = 'publishing', "
                    "publication_attempt_count = publication_attempt_count + 1, "
                    "representation_identity = :representation_identity, "
                    "publication_artifact_path = :artifact_path, "
                    "publication_built_at = :built_at, "
                    "publication_predecessor_captured = true, "
                    "publication_predecessor = CAST(:predecessor AS jsonb), "
                    "publication_started_at = :started_at, "
                    "publication_finished_at = NULL, "
                    "publication_error_type = NULL, "
                    "publication_error_message = NULL "
                    "WHERE id = :id"
                ),
                {
                    "id": run_id,
                    "representation_identity": representation_identity,
                    "artifact_path": artifact_path,
                    "built_at": built_at,
                    "predecessor": _json.dumps(requested_predecessor),
                    "started_at": datetime.datetime.now(datetime.UTC),
                },
            )

    async def record_publication_failure(
        self,
        run_id: str,
        error: BaseException,
    ) -> None:
        """Record a bounded retryable publication failure without failing work."""
        error_type, error_message = _bounded_failure(error)
        async with self._sf() as session, session.begin():
            result = await session.execute(
                text(
                    "UPDATE decomp_run SET publication_state = 'failed', "
                    "publication_error_type = :error_type, "
                    "publication_error_message = :error_message "
                    "WHERE id = :id AND status = 'running' "
                    "AND publication_state = 'publishing'"
                ),
                {
                    "id": run_id,
                    "error_type": error_type,
                    "error_message": error_message,
                },
            )
            if not result.rowcount:  # type: ignore[attr-defined]
                raise RunStateError(
                    f"decomposition run {run_id!r} has no active publication"
                )

    async def list_runs(self, limit: int = 50, offset: int = 0) -> list[RunSummary]:
        sql = text(
            "SELECT id, branch, status, ncit_version, started_at, finished_at, "
            "source_identity, fingerprint_sha256, emitted_at, error_type, "
            "error_message, publication_state, publication_attempt_count, "
            "representation_identity, publication_artifact_path, "
            "publication_built_at, publication_started_at, "
            "publication_finished_at, publication_error_type, "
            "publication_error_message, publication_predecessor_captured, "
            "publication_predecessor, metrics, "
            "(fingerprint ->> 'rehearsal_nonce') IS NOT NULL AS rehearsal "
            "FROM decomp_run ORDER BY started_at DESC LIMIT :limit OFFSET :offset"
        )
        async with self._sf() as s:
            result = await s.execute(sql, {"limit": limit, "offset": offset})
            return [self._row_to_run(r) for r in result.mappings().all()]

    async def get_run(self, run_id: str) -> RunSummary | None:
        sql = text(
            "SELECT id, branch, status, ncit_version, started_at, finished_at, "
            "source_identity, fingerprint_sha256, emitted_at, error_type, "
            "error_message, publication_state, publication_attempt_count, "
            "representation_identity, publication_artifact_path, "
            "publication_built_at, publication_started_at, "
            "publication_finished_at, publication_error_type, "
            "publication_error_message, publication_predecessor_captured, "
            "publication_predecessor, metrics, "
            "(fingerprint ->> 'rehearsal_nonce') IS NOT NULL AS rehearsal "
            "FROM decomp_run WHERE id = :run_id"
        )
        async with self._sf() as s:
            result = await s.execute(sql, {"run_id": run_id})
            row = result.mappings().first()
            return self._row_to_run(row) if row is not None else None

    async def list_minted_concepts(
        self,
        run_id: str | None = None,
        status: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[MintedConcept]:
        sql = text(
            "SELECT id, run_id, axis, label, source_signal, status FROM minted_concept "
            "WHERE (:run_id IS NULL OR run_id = :run_id) "
            "AND (:status IS NULL OR status = :status) "
            "ORDER BY id LIMIT :limit OFFSET :offset"
        )
        async with self._sf() as s:
            result = await s.execute(
                sql,
                {
                    "run_id": run_id,
                    "status": status,
                    "limit": limit,
                    "offset": offset,
                },
            )
            return [MintedConcept(**dict(r)) for r in result.mappings().all()]

    @staticmethod
    def _row_to_run(row: RowMapping) -> RunSummary:
        raw_metrics = row["metrics"]
        metrics = _validated_metrics({} if raw_metrics is None else raw_metrics)
        raw_predecessor = row.get("publication_predecessor")
        try:
            predecessor = (
                PublicationMarkerSnapshot.model_validate_json(
                    _json.dumps(raw_predecessor)
                )
                if raw_predecessor is not None
                else None
            )
        except ValidationError as exc:
            raise RunStateError(
                "persisted publication predecessor violates its schema"
            ) from exc
        return RunSummary(
            id=row["id"],
            branch=row["branch"],
            status=row["status"],
            rehearsal=row["rehearsal"],
            ncit_version=row["ncit_version"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            source_identity=row.get("source_identity"),
            fingerprint_sha256=row.get("fingerprint_sha256"),
            emitted_at=row.get("emitted_at"),
            error_type=row.get("error_type"),
            error_message=row.get("error_message"),
            publication_state=row.get("publication_state", "legacy"),
            publication_attempt_count=row.get("publication_attempt_count", 0),
            representation_identity=row.get("representation_identity"),
            publication_artifact_path=row.get("publication_artifact_path"),
            publication_built_at=row.get("publication_built_at"),
            publication_started_at=row.get("publication_started_at"),
            publication_finished_at=row.get("publication_finished_at"),
            publication_error_type=row.get("publication_error_type"),
            publication_error_message=row.get("publication_error_message"),
            publication_predecessor_captured=row.get(
                "publication_predecessor_captured", False
            ),
            publication_predecessor=predecessor,
            total_in_scope=metrics.total_in_scope,
            decomposed=metrics.decomposed,
            residual=metrics.residual,
            semantic_excluded=metrics.semantic_excluded,
            atomic_noop=metrics.atomic_noop,
            unknown_outcome=metrics.unknown_outcome,
            residual_precoordinated_count=metrics.residual_precoordinated_count,
            residual_precoordination_unknown_count=(
                metrics.residual_precoordination_unknown_count
            ),
            residual_precoordination=metrics.residual_precoordination,
            minted_count=metrics.minted_count,
            complete_definition_count=metrics.complete_definition_count,
            complete_fact_count=metrics.complete_fact_count,
            projected_fact_count=metrics.projected_fact_count,
            projection_loss_count=metrics.projection_loss_count,
            projection_loss_rate=metrics.projection_loss_rate,
            pct_decomposed=metrics.pct_decomposed,
            roundtrip_fidelity=metrics.roundtrip_fidelity,
        )

    async def finish_run(
        self,
        run_id: str,
        *,
        source_identity: str,
        metrics: dict[str, object],
        representation_identity: str | None = None,
    ) -> bool:
        """Complete only after exact work and requested publication completed.

        The same transaction promotes the run's mint proposals into the global
        ``minted_concept`` curator queue (D48: proposals become curator-visible only
        on success).
        """
        updated = False
        try:
            async with self._sf() as session, session.begin():
                locked = await session.execute(
                    text(
                        "SELECT status, source_identity, fingerprint, "
                        "fingerprint_sha256, publication_state, "
                        "representation_identity "
                        "FROM decomp_run "
                        "WHERE id = :id FOR UPDATE"
                    ),
                    {"id": run_id},
                )
                row = locked.mappings().first()
                if row is None:
                    return False
                _require_completion_source(row, source_identity)
                fingerprint = self._validated_fingerprint(
                    row["fingerprint"], row["fingerprint_sha256"]
                )
                await self._require_materialized_worklist(session, run_id, fingerprint)
                if row["status"] != "running":
                    raise RunStateError(f"decomposition run {run_id!r} is not running")
                incomplete = await session.execute(
                    text(
                        "SELECT count(*) FROM decomp_work_item "
                        "WHERE run_id = :id AND state <> 'complete'"
                    ),
                    {"id": run_id},
                )
                if incomplete.scalar_one() != 0:
                    raise RunStateError(
                        f"decomposition run {run_id!r} has unfinished work items"
                    )
                await _require_persisted_completion_counts(session, run_id)
                _require_completion_publication(row, representation_identity, run_id)
                completion_metrics = CompletionRunMetrics.model_validate(metrics)
                _require_matching_completion_metrics(
                    completion_metrics,
                    await _persisted_outcome_counts(session, run_id),
                    await _persisted_definition_counts(session, run_id),
                )
                metrics = completion_metrics.model_dump()
                await _promote_mint_proposals(session, run_id, fingerprint)
                result = await session.execute(
                    text(
                        "UPDATE decomp_run SET status = 'complete', "
                        "finished_at = :finished_at, "
                        "publication_state = CASE "
                        "WHEN publication_state = 'publishing' THEN 'published' "
                        "ELSE publication_state END, "
                        "publication_finished_at = CASE "
                        "WHEN publication_state = 'publishing' THEN :finished_at "
                        "ELSE publication_finished_at END, "
                        "metrics = CAST(:metrics AS jsonb) WHERE id = :id "
                        "AND status = 'running'"
                    ),
                    {
                        "id": run_id,
                        "finished_at": datetime.datetime.now(datetime.UTC),
                        "metrics": _json.dumps(metrics),
                    },
                )
                updated = bool(
                    cast("int", result.rowcount)  # type: ignore[attr-defined]
                )
        except asyncio.CancelledError:
            raise
        except Exception as original:
            if await _reconcile_finish_run_error(
                self._sf,
                run_id,
                updated=updated,
                source_identity=source_identity,
                metrics=metrics,
                representation_identity=representation_identity,
                original=original,
            ):
                return True
            raise
        return updated
