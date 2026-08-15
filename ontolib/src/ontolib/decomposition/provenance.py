"""Provenance persistence for decomposition runs (design section 4.5)."""

from __future__ import annotations

import asyncio
import datetime
import json as _json
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from sqlalchemy.engine import RowMapping
    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

    from ontolib.decomposition.minting import MintedConcept as MintedProposal
    from ontolib.decomposition.models import (
        CompleteDefinition,
        Constituent,
        Decomposition,
    )

from ontolib.decomposition.models import (
    CompleteDefinition,
    ConceptOutcome,
    Constituent,
    Decomposition,
    DefinitionGroup,
    GenusDefinitionFact,
    RestrictionDefinitionFact,
    SourceDefinitionOccurrence,
)
from ontolib.decomposition.provenance_models import (
    CompletedRunForEvidence,
    CompletionRunMetrics,
    MintedConcept,
    PersistedRunMetrics,
    PublicationMarkerSnapshot,
    RunFingerprint,
    RunOutcomeCounts,
    RunResumeIdentity,
    RunSummary,
    WorkItemOutcome,
)

_logger = logging.getLogger(__name__)
_PUBLICATION_LOCK_KEY = "decomposition:publication"


class RunStateError(RuntimeError):
    """A requested run/work-item transition is not currently valid."""


class RunIdentityMismatchError(RuntimeError):
    """A resume or completion attempted to cross an immutable run identity."""


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
        if outcome in {"semantic-excluded", "atomic-no-op"}:
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
    if resolved == "unknown" or resolved != expected:
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
            "source_role": constituent.source_role,
            "most_specific": constituent.most_specific,
            "needs_review": constituent.needs_review,
            "relationship_group": constituent.group,
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
    minted: tuple[MintedProposal, ...],
) -> None:
    await _insert_completion_rows(
        session,
        "INSERT INTO decomp_constituent "
        "(run_id, concept_code, axis, filler_code, axis_source, source_role, "
        "most_specific, needs_review, relationship_group, source_definition_ids) "
        "VALUES (:run_id, :concept_code, :axis, :filler_code, :axis_source, "
        ":source_role, :most_specific, :needs_review, :relationship_group, "
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
            "SELECT concept_code, axis, filler_code, axis_source, source_role, "
            "most_specific, needs_review, relationship_group, source_definition_ids "
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
    return (
        work_items.mappings().all(),
        constituent_result.mappings().all(),
        definition_result.mappings().all(),
        group_result.mappings().all(),
        edge_result.mappings().all(),
        occurrence_result.mappings().all(),
        occurrence_link_result.mappings().all(),
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
        by_code.setdefault(row["concept_code"], []).append(
            Constituent(
                axis=row["axis"],
                filler_code=row["filler_code"],
                axis_source=row["axis_source"],
                source_role=row["source_role"],
                most_specific=row["most_specific"],
                needs_review=row["needs_review"],
                group=row["relationship_group"],
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
        now = datetime.datetime.now(datetime.UTC)
        async with self._sf() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO decomp_run "
                    "(id, branch, status, ncit_version, started_at, "
                    "source_identity, fingerprint, fingerprint_sha256, emitted_at, "
                    "publication_state) "
                    "VALUES (:id, :branch, 'running', :ncit_version, :started_at, "
                    ":source_identity, CAST(:fingerprint AS jsonb), "
                    ":fingerprint_sha256, :emitted_at, :publication_state)"
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
                        "not_requested"
                        if fingerprint.output_mode == "none"
                        else "pending"
                    ),
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
            await self._require_materialized_worklist(session, run_id, fingerprint)
            actual = RunResumeIdentity.from_fingerprint(fingerprint)
            if actual != expected:
                dimension = (
                    "source identity"
                    if actual.source_identity != expected.source_identity
                    else "configuration"
                )
                raise RunIdentityMismatchError(
                    f"resume {dimension} does not match persisted run {run_id!r}"
                )
            await session.execute(
                text(
                    "UPDATE decomp_run SET status = 'running', "
                    "error_type = NULL, error_message = NULL "
                    "WHERE id = :id"
                ),
                {"id": run_id},
            )
            await session.execute(
                text(
                    "UPDATE decomp_work_item SET state = 'failed', "
                    "claim_token = NULL, claimed_at = NULL, "
                    "error_type = 'InterruptedRun', "
                    "error_message = 'Prior worker did not finish its claim', "
                    "failed_at = :failed_at "
                    "WHERE run_id = :id AND state = 'running'"
                ),
                {"id": run_id, "failed_at": datetime.datetime.now(datetime.UTC)},
            )
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

    async def outcome_counts(self, run_id: str) -> RunOutcomeCounts:
        """Return cumulative counters over the materialized exact worklist."""
        async with self._sf() as session:
            return await _persisted_outcome_counts(session, run_id)

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
            "publication_predecessor, metrics "
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
            "publication_predecessor, metrics "
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
                await session.execute(
                    text(
                        "INSERT INTO minted_concept "
                        "(id, run_id, axis, label, source_signal, status) "
                        "SELECT proposal_id, run_id, axis, label, source_signal, "
                        "status "
                        "FROM decomp_minted_proposal WHERE run_id = :id "
                        # Insert-or-ignore, never insert-or-update: a rerun re-mints the
                        # same deterministic proposal id with status='proposed', and
                        # promotion must never clobber a curator's earlier approve or
                        # reject decision (design section 7.2).
                        "ON CONFLICT (id) DO NOTHING"
                    ),
                    {"id": run_id},
                )
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
