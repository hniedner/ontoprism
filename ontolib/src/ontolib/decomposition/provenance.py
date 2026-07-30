"""Provenance persistence for decomposition runs (design section 4.5)."""

from __future__ import annotations

import datetime
import json as _json
import logging
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.engine import RowMapping
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from ontolib.decomposition.minting import MintedConcept as MintedProposal
    from ontolib.decomposition.models import (
        CompleteDefinition,
        Constituent,
        Decomposition,
    )

from ontolib.decomposition.models import (
    CompleteDefinition,
    Constituent,
    Decomposition,
    GenusDefinitionFact,
    RestrictionDefinitionFact,
)
from ontolib.decomposition.provenance_models import (
    MintedConcept,
    RunFingerprint,
    RunOutcomeCounts,
    RunResumeIdentity,
    RunSummary,
)

_logger = logging.getLogger(__name__)


class RunStateError(RuntimeError):
    """A requested run/work-item transition is not currently valid."""


class RunIdentityMismatchError(RuntimeError):
    """A resume or completion attempted to cross an immutable run identity."""


def _bounded_failure(error: BaseException) -> tuple[str, str]:
    error_type = type(error).__name__[:128] or "Exception"
    message = str(error)[:1000] or error_type
    return error_type, message


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


def _completion_outcome(
    concept_code: str,
    decomposition: Decomposition | None,
    minted: tuple[MintedProposal, ...],
) -> tuple[list[Constituent], bool, bool]:
    _require_matching_decomposition(concept_code, decomposition)
    _require_decomposition_for_mints(decomposition, minted)
    constituents = decomposition.constituents if decomposition is not None else []
    has_decomposition = decomposition is not None
    return (
        constituents,
        has_decomposition and bool(constituents),
        has_decomposition and not constituents,
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


class ProvenanceStore:
    """Persistence for decomposition run manifests and constituents."""

    def __init__(self, sf: async_sessionmaker[AsyncSession]) -> None:
        self._sf = sf

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
                    "source_identity, fingerprint, fingerprint_sha256, emitted_at) "
                    "VALUES (:id, :branch, 'running', :ncit_version, :started_at, "
                    ":source_identity, CAST(:fingerprint AS jsonb), "
                    ":fingerprint_sha256, :emitted_at)"
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
            legacy = (
                isinstance(raw, dict)
                and raw.get("schema_version") == 0
                and persisted_identity == "0" * 64
            )
            detail = (
                "predates the exact-run schema"
                if legacy
                else "is corrupt or was modified outside the pipeline"
            )
            raise RunIdentityMismatchError(
                f"persisted run fingerprint {detail}"
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
    ) -> None:
        """Replace one concept's rows and mark it complete in one transaction."""
        constituents, is_decomposed, is_residual = _completion_outcome(
            concept_code, decomposition, minted
        )
        complete_definition = (
            decomposition.complete_definition if decomposition is not None else None
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
            await session.execute(
                text(
                    "DELETE FROM decomp_definition_fact "
                    "WHERE run_id = :run_id AND concept_code = :concept_code"
                ),
                {"run_id": run_id, "concept_code": concept_code},
            )
            await session.execute(
                text(
                    "DELETE FROM decomp_constituent "
                    "WHERE run_id = :run_id AND concept_code = :concept_code"
                ),
                {"run_id": run_id, "concept_code": concept_code},
            )
            await session.execute(
                text(
                    "DELETE FROM decomp_minted_proposal "
                    "WHERE run_id = :run_id AND concept_code = :concept_code"
                ),
                {"run_id": run_id, "concept_code": concept_code},
            )
            if constituents:
                await session.execute(
                    text(
                        "INSERT INTO decomp_constituent "
                        "(run_id, concept_code, axis, filler_code, axis_source, "
                        "most_specific, needs_review, relationship_group, "
                        "source_definition_ids) VALUES "
                        "(:run_id, :concept_code, :axis, :filler_code, "
                        ":axis_source, :most_specific, :needs_review, "
                        ":relationship_group, CAST(:source_definition_ids AS jsonb))"
                    ),
                    _constituent_rows(run_id, concept_code, constituents),
                )
            definition_rows = _definition_fact_rows(
                run_id,
                concept_code,
                complete_definition,
            )
            if definition_rows:
                await session.execute(
                    text(
                        "INSERT INTO decomp_definition_fact "
                        "(run_id, concept_code, fact_id, anchor_code, group_id, "
                        "depth, fact_kind, genus_code, is_defined, role_code, "
                        "filler_code) VALUES "
                        "(:run_id, :concept_code, :fact_id, :anchor_code, "
                        ":group_id, :depth, :fact_kind, :genus_code, "
                        ":is_defined, :role_code, :filler_code)"
                    ),
                    definition_rows,
                )
            if minted:
                await session.execute(
                    text(
                        "INSERT INTO decomp_minted_proposal "
                        "(run_id, concept_code, proposal_id, axis, label, "
                        "source_signal, status) VALUES "
                        "(:run_id, :concept_code, :proposal_id, :axis, :label, "
                        ":source_signal, :status)"
                    ),
                    _proposal_rows(run_id, concept_code, minted),
                )
            updated = await session.execute(
                text(
                    "UPDATE decomp_work_item SET state = 'complete', "
                    "claim_token = NULL, claimed_at = NULL, "
                    "semantic_type = :semantic_type, "
                    "is_decomposed = :is_decomposed, is_residual = :is_residual, "
                    "constituent_count = :constituent_count, "
                    "minted_count = :minted_count, completed_at = :completed_at "
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
                        else None
                    ),
                    "is_decomposed": is_decomposed,
                    "is_residual": is_residual,
                    "constituent_count": len(constituents),
                    "minted_count": len(minted),
                    "completed_at": datetime.datetime.now(datetime.UTC),
                },
            )
            if not cast("int", updated.rowcount):  # type: ignore[attr-defined]
                raise RunStateError("work-item claim changed before completion")

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
            await session.execute(
                text("DELETE FROM decomp_definition_fact WHERE run_id = :run_id"),
                {"run_id": run_id},
            )
            await session.execute(
                text("DELETE FROM decomp_constituent WHERE run_id = :run_id"),
                {"run_id": run_id},
            )
            await session.execute(
                text("DELETE FROM decomp_minted_proposal WHERE run_id = :run_id"),
                {"run_id": run_id},
            )
            await session.execute(
                text(
                    "UPDATE decomp_work_item SET state = 'failed', "
                    "claim_token = NULL, claimed_at = NULL, semantic_type = NULL, "
                    "is_decomposed = NULL, is_residual = NULL, "
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
            work_items = await session.execute(
                text(
                    "SELECT concept_code, semantic_type FROM decomp_work_item "
                    "WHERE run_id = :run_id AND state = 'complete' "
                    "AND is_decomposed ORDER BY ordinal"
                ),
                {"run_id": run_id},
            )
            constituent_result = await session.execute(
                text(
                    "SELECT concept_code, axis, filler_code, axis_source, "
                    "most_specific, needs_review, relationship_group, "
                    "source_definition_ids FROM decomp_constituent "
                    "WHERE run_id = :run_id "
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
            work_item_rows = work_items.mappings().all()
            constituent_rows = constituent_result.mappings().all()
            definition_rows = definition_result.mappings().all()

        constituents_by_code: dict[str, list[Constituent]] = {}
        for row in constituent_rows:
            raw_source_ids = row["source_definition_ids"]
            if isinstance(raw_source_ids, str):
                raw_source_ids = _json.loads(raw_source_ids)
            constituents_by_code.setdefault(row["concept_code"], []).append(
                Constituent(
                    axis=row["axis"],
                    filler_code=row["filler_code"],
                    axis_source=row["axis_source"],
                    most_specific=row["most_specific"],
                    needs_review=row["needs_review"],
                    group=row["relationship_group"],
                    source_definition_ids=tuple(raw_source_ids),
                )
            )

        facts_by_code: dict[
            str, list[GenusDefinitionFact | RestrictionDefinitionFact]
        ] = {}
        for row in definition_rows:
            common = {
                "fact_id": row["fact_id"],
                "anchor_code": row["anchor_code"],
                "group_id": row["group_id"],
                "depth": row["depth"],
            }
            if row["fact_kind"] == "genus":
                fact = GenusDefinitionFact(
                    **common,
                    genus_code=row["genus_code"],
                    is_defined=row["is_defined"],
                )
            else:
                fact = RestrictionDefinitionFact(
                    **common,
                    role_code=row["role_code"],
                    filler_code=row["filler_code"],
                )
            facts_by_code.setdefault(row["concept_code"], []).append(fact)

        return [
            Decomposition(
                code=row["concept_code"],
                semantic_type=row["semantic_type"],
                constituents=constituents_by_code.get(row["concept_code"], []),
                complete_definition=(
                    CompleteDefinition(
                        root_code=row["concept_code"],
                        facts=tuple(facts_by_code[row["concept_code"]]),
                    )
                    if row["concept_code"] in facts_by_code
                    else None
                ),
            )
            for row in work_item_rows
        ]

    async def outcome_counts(self, run_id: str) -> RunOutcomeCounts:
        """Return cumulative counters over the materialized exact worklist."""
        async with self._sf() as session:
            result = await session.execute(
                text(
                    "SELECT count(*) AS total_in_scope, "
                    "count(*) FILTER (WHERE is_decomposed) AS decomposed, "
                    "count(*) FILTER (WHERE is_residual) AS residual, "
                    "COALESCE(sum(minted_count), 0) AS minted_count "
                    "FROM decomp_work_item WHERE run_id = :run_id"
                ),
                {"run_id": run_id},
            )
            row = result.mappings().one()
            return RunOutcomeCounts.model_validate(dict(row))

    async def list_runs(self, limit: int = 50, offset: int = 0) -> list[RunSummary]:
        sql = text(
            "SELECT id, branch, status, ncit_version, started_at, finished_at, "
            "source_identity, fingerprint_sha256, emitted_at, error_type, "
            "error_message, metrics "
            "FROM decomp_run ORDER BY started_at DESC LIMIT :limit OFFSET :offset"
        )
        async with self._sf() as s:
            result = await s.execute(sql, {"limit": limit, "offset": offset})
            return [self._row_to_run(r) for r in result.mappings().all()]

    async def get_run(self, run_id: str) -> RunSummary | None:
        sql = text(
            "SELECT id, branch, status, ncit_version, started_at, finished_at, "
            "source_identity, fingerprint_sha256, emitted_at, error_type, "
            "error_message, metrics "
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
        raw = row["metrics"]
        if isinstance(raw, str):
            try:
                m = _json.loads(raw)
            except _json.JSONDecodeError:
                _logger.warning(
                    "Corrupt metrics JSON in decomp_run %s", row.get("id", "?")
                )
                m = {}
        else:
            m = raw or {}
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
            total_in_scope=m.get("total_in_scope"),
            decomposed=m.get("decomposed"),
            residual=m.get("residual"),
            residual_precoordinated_count=m.get("residual_precoordinated_count"),
            residual_precoordination=_residual_precoordination_metric(m),
            minted_count=m.get("minted_count"),
            complete_definition_count=m.get("complete_definition_count"),
            complete_fact_count=m.get("complete_fact_count"),
            projected_fact_count=m.get("projected_fact_count"),
            projection_loss_count=m.get("projection_loss_count"),
            projection_loss_rate=m.get("projection_loss_rate"),
            pct_decomposed=m.get("pct_decomposed"),
            roundtrip_fidelity=m.get("roundtrip_fidelity"),
        )

    async def finish_run(
        self,
        run_id: str,
        *,
        source_identity: str,
        metrics: dict[str, object],
    ) -> bool:
        """Complete only the matching run after every exact work item completed.

        The same transaction promotes the run's mint proposals into the global
        ``minted_concept`` curator queue (D48: proposals become curator-visible only
        on success).
        """
        async with self._sf() as session, session.begin():
            locked = await session.execute(
                text(
                    "SELECT status, source_identity, fingerprint, fingerprint_sha256 "
                    "FROM decomp_run "
                    "WHERE id = :id FOR UPDATE"
                ),
                {"id": run_id},
            )
            row = locked.mappings().first()
            if row is None:
                return False
            if row["source_identity"] != source_identity:
                raise RunIdentityMismatchError(
                    "completion source identity does not match persisted run"
                )
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
            await session.execute(
                text(
                    "INSERT INTO minted_concept "
                    "(id, run_id, axis, label, source_signal, status) "
                    "SELECT proposal_id, run_id, axis, label, source_signal, status "
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
                    "metrics = CAST(:metrics AS jsonb) WHERE id = :id "
                    "AND status = 'running'"
                ),
                {
                    "id": run_id,
                    "finished_at": datetime.datetime.now(datetime.UTC),
                    "metrics": _json.dumps(metrics),
                },
            )
            return bool(cast("int", result.rowcount))  # type: ignore[attr-defined]
