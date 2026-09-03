"""Read-only proof-bound preview of production decomposition resume selection."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import text

from ontolib.decomposition.pre_resume import (
    PreResumeProof,
    ordered_code_identity,
    pre_resume_proof_identity,
)
from ontolib.decomposition.provenance import ProvenanceStore
from ontolib.decomposition.provenance_models import RunFingerprint, RunResumeIdentity

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncEngine


EXPECTED_PRE_RESUME_PROOF_IDENTITY = (
    "f3c321c38deb8478f7a1abfa5c1edb1ef9ac3daf793d0dfe8d1e758eb62d2018"
)
EXPECTED_OUTPUT_PATH = Path("tmp/neoplasm-r101-v4-full.ttl")
EXPECTED_PENDING_COUNT = 9733
EXPECTED_COMPLETED_COUNT = 5900
_FRESHNESS_FIELDS = frozenset(
    {"observed_at", "postgres_reads", "qlever_reads", "artifact_path"}
)


class ResumeWorkItem:
    """Persisted fields needed to prove one work item is safe to select or exclude."""

    concept_code: str
    ordinal: int
    state: Literal["pending", "running", "failed", "complete"]
    attempt_count: int
    claim_token: object | None
    claimed_at: object | None
    semantic_type: str | None
    semantic_types: tuple[str, ...] | None
    outcome: str | None
    is_decomposed: bool | None
    is_residual: bool | None
    has_complete_definition: bool | None
    constituent_count: int | None
    minted_count: int | None
    error_type: str | None
    error_message: str | None
    failed_at: object | None
    completed_at: object | None

    def __init__(
        self,
        *,
        concept_code: str,
        ordinal: int,
        state: Literal["pending", "running", "failed", "complete"],
        attempt_count: int,
        claim_token: object | None = None,
        claimed_at: object | None = None,
        semantic_type: str | None = None,
        semantic_types: tuple[str, ...] | None = None,
        outcome: str | None = None,
        is_decomposed: bool | None = None,
        is_residual: bool | None = None,
        has_complete_definition: bool | None = None,
        constituent_count: int | None = None,
        minted_count: int | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
        failed_at: object | None = None,
        completed_at: object | None = None,
    ) -> None:
        self.concept_code = concept_code
        self.ordinal = ordinal
        self.state = state
        self.attempt_count = attempt_count
        self.claim_token = claim_token
        self.claimed_at = claimed_at
        self.semantic_type = semantic_type
        self.semantic_types = semantic_types
        self.outcome = outcome
        self.is_decomposed = is_decomposed
        self.is_residual = is_residual
        self.has_complete_definition = has_complete_definition
        self.constituent_count = constituent_count
        self.minted_count = minted_count
        self.error_type = error_type
        self.error_message = error_message
        self.failed_at = failed_at
        self.completed_at = completed_at


class ResumeSelection:
    """Exact ordinal cohorts observed by the production resume selector."""

    def __init__(
        self,
        *,
        fingerprint: RunFingerprint,
        pending_codes: tuple[str, ...],
        completed_codes: tuple[str, ...],
        selected_complete_count: int,
        postgres_reads: int,
    ) -> None:
        self.fingerprint = fingerprint
        self.pending_codes = pending_codes
        self.completed_codes = completed_codes
        self.selected_complete_count = selected_complete_count
        self.postgres_reads = postgres_reads


def _pending_is_pristine(row: ResumeWorkItem) -> bool:
    nullable = (
        row.claim_token,
        row.claimed_at,
        row.semantic_type,
        row.semantic_types,
        row.outcome,
        row.is_decomposed,
        row.is_residual,
        row.constituent_count,
        row.minted_count,
        row.error_type,
        row.error_message,
        row.failed_at,
        row.completed_at,
    )
    return (
        row.attempt_count == 0
        and row.has_complete_definition is False
        and all(value is None for value in nullable)
    )


def _complete_is_closed(row: ResumeWorkItem) -> bool:
    required = (
        row.semantic_types,
        row.outcome,
        row.is_decomposed,
        row.is_residual,
        row.has_complete_definition,
        row.constituent_count,
        row.minted_count,
        row.completed_at,
    )
    forbidden = (
        row.claim_token,
        row.claimed_at,
        row.error_type,
        row.error_message,
        row.failed_at,
    )
    return (
        row.attempt_count > 0
        and all(value is not None for value in required)
        and all(value is None for value in forbidden)
    )


def _append_validated_item(
    row: ResumeWorkItem, pending: list[str], completed: list[str]
) -> None:
    if row.state == "pending":
        if row.claim_token is not None:
            raise ValueError("pending work item retains a claim token")
        if not _pending_is_pristine(row):
            raise ValueError("pending work item attempt or metadata drift")
        pending.append(row.concept_code)
        return
    if row.state == "complete":
        if not _complete_is_closed(row):
            raise ValueError("complete work item completion metadata is incomplete")
        completed.append(row.concept_code)
        return
    raise ValueError("resume selection contains running or failed work")


def _require_selection_context(
    fingerprint: RunFingerprint,
    expected_identity: RunResumeIdentity,
    work_items: Sequence[ResumeWorkItem],
    integrity_counts: Mapping[str, int],
    postgres_reads: int,
) -> None:
    ProvenanceStore.require_resume_identity(
        fingerprint, expected_identity, "dry-run resume"
    )
    if postgres_reads <= 0:
        raise ValueError("resume inspection must execute PostgreSQL reads")
    if any(integrity_counts.values()):
        raise ValueError("resume child/completion integrity mismatch")
    if tuple(row.ordinal for row in work_items) != tuple(range(len(work_items))):
        raise ValueError("resume worklist ordinal overlap or gap")
    if tuple(row.concept_code for row in work_items) != fingerprint.worklist:
        raise ValueError("materialized worklist does not match fingerprint")


def validate_resume_selection(
    *,
    fingerprint: RunFingerprint,
    expected_identity: RunResumeIdentity,
    work_items: Sequence[ResumeWorkItem],
    integrity_counts: Mapping[str, int],
    postgres_reads: int,
) -> ResumeSelection:
    """Apply production identity and non-complete selection semantics fail-closed."""
    _require_selection_context(
        fingerprint,
        expected_identity,
        work_items,
        integrity_counts,
        postgres_reads,
    )

    pending: list[str] = []
    completed: list[str] = []
    for row in work_items:
        _append_validated_item(row, pending, completed)
    return ResumeSelection(
        fingerprint=fingerprint,
        pending_codes=tuple(pending),
        completed_codes=tuple(completed),
        selected_complete_count=0,
        postgres_reads=postgres_reads,
    )


_RESUME_RUN_SQL = (
    "SELECT status, error_type, error_message, fingerprint, fingerprint_sha256 "
    "FROM decomp_run WHERE id = :run_id"
)
_RESUME_WORK_SQL = (
    "SELECT concept_code, ordinal, state, attempt_count, claim_token, claimed_at, "
    "semantic_type, semantic_types, outcome, is_decomposed, is_residual, "
    "has_complete_definition, constituent_count, minted_count, error_type, "
    "error_message, failed_at, completed_at FROM decomp_work_item "
    "WHERE run_id = :run_id ORDER BY ordinal"
)
_RESUME_INTEGRITY_SQL = (
    "SELECT "
    "(SELECT count(*) FROM decomp_work_item w WHERE w.run_id = :run_id AND "
    "((w.state = 'complete' AND (w.completed_at IS NULL OR w.constituent_count IS NULL "
    "OR w.minted_count IS NULL OR w.outcome IS NULL OR w.semantic_types IS NULL)) OR "
    "(w.state <> 'complete' AND (w.completed_at IS NOT NULL OR "
    "w.constituent_count IS NOT NULL OR w.minted_count IS NOT NULL)))) "
    "AS completion_metadata_mismatch_count, "
    "(SELECT count(*) FROM decomp_work_item w WHERE w.run_id = :run_id "
    "AND w.state = 'complete' AND w.constituent_count <> (SELECT count(*) "
    "FROM decomp_constituent c WHERE c.run_id = w.run_id AND "
    "c.concept_code = w.concept_code)) AS constituent_count_mismatch_count, "
    "(SELECT count(*) FROM decomp_work_item w WHERE w.run_id = :run_id "
    "AND w.state = 'complete' AND w.minted_count <> (SELECT count(*) "
    "FROM decomp_minted_proposal m WHERE m.run_id = w.run_id AND "
    "m.concept_code = w.concept_code)) AS minted_count_mismatch_count, "
    "(SELECT count(*) FROM ("
    "SELECT c.run_id, c.concept_code FROM decomp_constituent c LEFT JOIN "
    "decomp_work_item w USING (run_id, concept_code) WHERE c.run_id = :run_id "
    "AND w.run_id IS NULL UNION ALL SELECT m.run_id, m.concept_code FROM "
    "decomp_minted_proposal m LEFT JOIN decomp_work_item w USING "
    "(run_id, concept_code) WHERE m.run_id = :run_id AND w.run_id IS NULL UNION ALL "
    "SELECT f.run_id, f.concept_code FROM decomp_definition_fact f LEFT JOIN "
    "decomp_work_item w USING (run_id, concept_code) WHERE f.run_id = :run_id "
    "AND w.run_id IS NULL UNION ALL SELECT g.run_id, g.concept_code FROM "
    "decomp_definition_group g LEFT JOIN decomp_work_item w USING "
    "(run_id, concept_code) WHERE g.run_id = :run_id AND w.run_id IS NULL UNION ALL "
    "SELECT e.run_id, e.concept_code FROM decomp_definition_group_edge e LEFT JOIN "
    "decomp_work_item w USING (run_id, concept_code) WHERE e.run_id = :run_id "
    "AND w.run_id IS NULL UNION ALL SELECT o.run_id, o.concept_code FROM "
    "decomp_source_occurrence o LEFT JOIN decomp_work_item w USING "
    "(run_id, concept_code) WHERE o.run_id = :run_id AND w.run_id IS NULL UNION ALL "
    "SELECT co.run_id, co.concept_code FROM decomp_constituent_occurrence co LEFT JOIN "
    "decomp_work_item w USING (run_id, concept_code) WHERE co.run_id = :run_id "
    "AND w.run_id IS NULL) orphan) AS child_orphan_count"
)


async def inspect_resume_selection(
    engine: AsyncEngine,
    run_id: str,
    expected_identity: RunResumeIdentity,
) -> tuple[ResumeSelection, tuple[str, str, str]]:
    """Execute exactly three SELECTs in a read-only repeatable-read transaction."""
    connection = await engine.connect()
    connection = await connection.execution_options(
        isolation_level="REPEATABLE READ", postgresql_readonly=True
    )
    try:
        async with connection.begin():
            run_result = await connection.execute(
                text(_RESUME_RUN_SQL), {"run_id": run_id}
            )
            run_row = run_result.mappings().one()
            work_result = await connection.execute(
                text(_RESUME_WORK_SQL), {"run_id": run_id}
            )
            work_rows = work_result.mappings().all()
            integrity_result = await connection.execute(
                text(_RESUME_INTEGRITY_SQL), {"run_id": run_id}
            )
            integrity = dict(integrity_result.mappings().one())
    finally:
        await connection.close()

    if run_row["status"] not in {"running", "failed"}:
        raise ValueError("decomposition run is not resumable")
    fingerprint = ProvenanceStore._validated_fingerprint(
        run_row["fingerprint"], run_row["fingerprint_sha256"]
    )
    rows = tuple(
        ResumeWorkItem(
            **{
                **dict(row),
                "semantic_types": (
                    tuple(row["semantic_types"])
                    if row["semantic_types"] is not None
                    else None
                ),
            }
        )
        for row in work_rows
    )
    selection = validate_resume_selection(
        fingerprint=fingerprint,
        expected_identity=expected_identity,
        work_items=rows,
        integrity_counts=integrity,
        postgres_reads=3,
    )
    failure = (run_row["status"], run_row["error_type"], run_row["error_message"])
    if not all(isinstance(value, str) for value in failure):
        raise ValueError("resumable run failure metadata is incomplete")
    return selection, failure  # type: ignore[return-value]


def canonical_resume_dry_run_json(payload: Mapping[str, Any]) -> str:
    """Canonical semantic bytes, excluding invocation-local freshness and location."""
    canonical = {
        key: value for key, value in payload.items() if key not in _FRESHNESS_FIELDS
    }
    canonical.pop("identity", None)
    return json.dumps(
        canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )


def resume_dry_run_identity(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_resume_dry_run_json(payload).encode()).hexdigest()


def _require_proof_bindings(
    run_id: str,
    proof: Mapping[str, Any],
    output_path: Path,
    fingerprint: RunFingerprint,
    failure: tuple[str, str, str],
    qlever_reads: int,
) -> None:
    if proof.get("proof_identity") != EXPECTED_PRE_RESUME_PROOF_IDENTITY:
        raise ValueError("wrong pre-resume proof identity")
    if output_path != EXPECTED_OUTPUT_PATH:
        raise ValueError("resume output path mismatch")
    bindings = {
        "run_id": run_id,
        "source_identity": fingerprint.source_identity,
        "fingerprint_identity": fingerprint.identity,
    }
    if any(proof.get(name) != value for name, value in bindings.items()):
        raise ValueError("pre-resume proof run/source/fingerprint mismatch")
    if failure != ("failed", "BrokenPipeError", "[Errno 32] Broken pipe"):
        raise ValueError("protected run failure metadata drift")
    if qlever_reads <= 0:
        raise ValueError("source freshness must execute QLever reads")


def _require_protected_cohorts(
    selection: ResumeSelection,
    proof: Mapping[str, Any],
    pending_digest: str,
    completed_digest: str,
) -> None:
    observed = (
        len(selection.pending_codes),
        proof.get("pending_count"),
        pending_digest,
        len(selection.completed_codes),
        proof.get("completed_count"),
        completed_digest,
    )
    expected = (
        EXPECTED_PENDING_COUNT,
        EXPECTED_PENDING_COUNT,
        proof.get("pending_cohort_digest"),
        EXPECTED_COMPLETED_COUNT,
        EXPECTED_COMPLETED_COUNT,
        proof.get("completed_cohort_digest"),
    )
    if observed != expected:
        raise ValueError("resume cohort count or digest drift")


def build_resume_dry_run(
    *,
    run_id: str,
    proof: Mapping[str, Any],
    semantic_identity: str,
    output_path: Path,
    selection: ResumeSelection,
    status: str,
    error_type: str,
    error_message: str,
    qlever_reads: int,
    artifact_path: Path | None = None,
) -> dict[str, Any]:
    """Bind a protected-run selection to its proof and current runner semantics."""
    if selection.selected_complete_count:
        raise ValueError("resume selector selected completed work")
    fingerprint = selection.fingerprint
    _require_proof_bindings(
        run_id,
        proof,
        output_path,
        fingerprint,
        (status, error_type, error_message),
        qlever_reads,
    )
    pending_digest = ordered_code_identity(selection.pending_codes)
    completed_digest = ordered_code_identity(selection.completed_codes)
    _require_protected_cohorts(selection, proof, pending_digest, completed_digest)

    dimensions = RunResumeIdentity.from_fingerprint(fingerprint).model_dump(mode="json")
    payload: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "status": status,
        "error_type": error_type,
        "error_message": error_message,
        "source_identity": fingerprint.source_identity,
        "fingerprint_identity": fingerprint.identity,
        "proof_identity": EXPECTED_PRE_RESUME_PROOF_IDENTITY,
        "pre_resume_semantic_identity": proof.get("semantic_identity"),
        "current_semantic_identity": semantic_identity,
        "branch": fingerprint.branch,
        "walker_max_depth": fingerprint.walker_max_depth,
        "output_path": output_path.as_posix(),
        "resume_identity_dimensions": dimensions,
        "pending_count": len(selection.pending_codes),
        "pending_attempt_count": 0,
        "pending_digest": pending_digest,
        "selected_complete_count": selection.selected_complete_count,
        "completed_exclusion_count": len(selection.completed_codes),
        "completed_exclusion_digest": completed_digest,
        "postgres_reads": selection.postgres_reads,
        "qlever_reads": qlever_reads,
        "observed_at": datetime.now(UTC).isoformat(),
    }
    if artifact_path is not None:
        payload["artifact_path"] = artifact_path.as_posix()
    payload["identity"] = resume_dry_run_identity(payload)
    return payload


def write_resume_dry_run(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def load_pre_resume_proof(path: Path) -> dict[str, Any]:
    """Load one strict proof and verify its canonical identity from file bytes."""

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate pre-resume proof key: {key}")
            result[key] = value
        return result

    raw = json.loads(path.read_text(), object_pairs_hook=reject_duplicates)
    if not isinstance(raw, dict):
        raise ValueError("pre-resume proof must be a JSON object")
    supplied_identity = raw.get("proof_identity")
    if supplied_identity != EXPECTED_PRE_RESUME_PROOF_IDENTITY:
        raise ValueError("wrong pre-resume proof identity")
    semantic_payload = {
        key: value
        for key, value in raw.items()
        if key not in {"observed_at", "proof_identity"}
    }
    proof = PreResumeProof.model_validate_json(
        json.dumps(semantic_payload, sort_keys=True, separators=(",", ":"))
    )
    payload = proof.model_dump(mode="json")
    if pre_resume_proof_identity(payload) != supplied_identity:
        raise ValueError("pre-resume proof identity does not match its payload")
    payload["proof_identity"] = supplied_identity
    return payload
