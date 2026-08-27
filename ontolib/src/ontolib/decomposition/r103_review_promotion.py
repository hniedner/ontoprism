"""Durable promotion of the complete, write-free R103 local-SME review state."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal, Self

from pydantic import Field, model_validator
from pydantic_core import to_jsonable_python

from ontolib.decomposition.proposal_registry import load_proposal_registry
from ontolib.decomposition.r103_review import (
    R103Decision,
    R103DecisionRegistry,
    R103ReviewDryRun,
    R103ReviewPacket,
    R103ReviewValidationError,
    _canonical,
    _file_sha256,
    _identity,
    _load_json,
    _StrictModel,
    _validate_dry_run_binding,
    _write_json,
    dry_run_r103_review,
    load_r103_decision_registry,
    load_r103_review_packet,
)

if TYPE_CHECKING:
    from pathlib import Path

_SHA256 = r"^[0-9a-f]{64}$"
_EXPECTED_REVIEWS = (
    (
        "source-supported",
        "C12950 is supported by C2860’s stated derivation from adrenal embryonic "  # noqa: RUF001
        "rest cells. It is anatomically broad but is the most specific currently "
        "available NCIt tissue-origin filler; no range-compatible, more specific "
        "NCIt replacement has been identified.",
        "R. Hannes Niedner, M.D.",
        "2026-08-26",
    ),
    (
        "review-required",
        "Fetal-tissue resemblance describes morphology, not necessarily normal "
        "tissue of origin, so the R103 assertion should not be projected without "
        "stronger evidence.",
        "R. Hannes Niedner, M.D.",
        "2026-08-26",
    ),
    (
        "source-supported",
        "C3716 explicitly states origin in neuroectoderm, and C34228 defines that "
        "embryologic tissue; the R103 assertion directly represents the stated "
        "normal tissue of origin while R104 separately preserves cell-origin context.",
        "R. Hannes Niedner, M.D.",
        "2026-08-26",
    ),
)


class R103PromotedReviewState(_StrictModel):
    """Complete durable local-SME state with file and semantic bindings."""

    schema_version: Literal[1]
    packet: R103ReviewPacket
    registry: R103DecisionRegistry
    dry_run: R103ReviewDryRun
    oracle_file_sha256: str = Field(pattern=_SHA256)
    proposal_registry_identity: str = Field(pattern=_SHA256)
    proposal_registry_file_sha256: str = Field(pattern=_SHA256)
    artifact_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _validate_promoted_state(self) -> Self:
        _validate_promoted_cross_bindings(self)
        expected = _identity(self.model_dump(exclude={"artifact_identity"}))
        if self.artifact_identity != expected:
            raise ValueError("promoted artifact identity differs")
        return self


def _validate_promoted_cross_bindings(state: R103PromotedReviewState) -> None:
    _validate_registry_bindings(state.packet, state.registry)
    _validate_review_values(state.packet, state.registry)
    _validate_dry_run_state(state)
    if state.proposal_registry_identity != state.packet.proposal_registry_identity:
        raise ValueError("proposal registry semantic binding differs")


def _validate_registry_bindings(
    packet: R103ReviewPacket, registry: R103DecisionRegistry
) -> None:
    _validate_dry_run_binding(packet, registry)
    if registry.workbook_identity != registry.decisions[0].workbook_identity:
        raise ValueError("decision registry workbook or source binding differs")
    for decision in registry.decisions:
        _validate_decision_binding(packet, registry, decision)
    packet_rows = tuple(
        (row.row_identity, row.subject_code, row.role_code, row.filler_code)
        for row in packet.rows
    )
    decision_rows = tuple(
        (row.row_identity, row.subject_code, row.role_code, row.filler_code)
        for row in registry.decisions
    )
    if decision_rows != packet_rows:
        raise ValueError("packet and decision row joins differ")


def _validate_decision_binding(
    packet: R103ReviewPacket,
    registry: R103DecisionRegistry,
    decision: R103Decision,
) -> None:
    observed = (
        decision.workbook_identity,
        decision.packet_identity,
        decision.source_identity,
        decision.source_release,
    )
    expected = (
        registry.workbook_identity,
        packet.packet_identity,
        packet.source_identity,
        packet.source_release,
    )
    if observed != expected:
        raise ValueError("decision registry workbook or source binding differs")


def _validate_review_values(
    packet: R103ReviewPacket, registry: R103DecisionRegistry
) -> None:
    human_rows = tuple(
        (row.outcome, row.rationale, row.reviewer, row.review_date)
        for row in registry.decisions
    )
    if human_rows != _EXPECTED_REVIEWS:
        raise ValueError("promoted human review values differ")
    expected_workbook_identity = _identity(
        {"packet_identity": packet.packet_identity, "human_rows": human_rows}
    )
    if registry.workbook_identity != expected_workbook_identity:
        raise ValueError("promoted workbook identity differs")
    outcomes = tuple((row.subject_code, row.outcome) for row in registry.decisions)
    if outcomes != (
        ("C2860", "source-supported"),
        ("C3264", "review-required"),
        ("C3716", "source-supported"),
    ):
        raise ValueError("promoted decision vector differs")


def _validate_dry_run_state(state: R103PromotedReviewState) -> None:
    dry_run = state.dry_run
    observed = (
        dry_run.writes_performed,
        dry_run.outcome_counts,
        dry_run.proposal_previews,
        dry_run.exclusion_previews,
        dry_run.unresolved,
        dry_run.readiness,
        dry_run.oracle_identity_before,
        dry_run.oracle_identity_after,
        dry_run.proposal_registry_identity_before,
        dry_run.proposal_registry_identity_after,
    )
    expected = (
        False,
        {"source-supported": 2, "review-required": 1},
        (),
        (),
        1,
        "review-incomplete",
        state.oracle_file_sha256,
        state.oracle_file_sha256,
        state.proposal_registry_file_sha256,
        state.proposal_registry_file_sha256,
    )
    if observed != expected:
        raise ValueError("promoted dry-run boundary differs")


def _load_r103_review_dry_run(path: Path) -> R103ReviewDryRun:
    try:
        return R103ReviewDryRun.model_validate_json(_canonical(_load_json(path)))
    except ValueError as error:
        raise R103ReviewValidationError(str(error)) from error


def load_r103_promoted_review_state(path: Path) -> R103PromotedReviewState:
    """Parse a strict promotion without reading its vanished staging inputs."""
    try:
        return R103PromotedReviewState.model_validate_json(_canonical(_load_json(path)))
    except ValueError as error:
        raise R103ReviewValidationError(str(error)) from error


def _render_json(value: object) -> bytes:
    return (
        json.dumps(
            to_jsonable_python(value), sort_keys=True, indent=2, ensure_ascii=True
        ).encode("ascii")
        + b"\n"
    )


def _load_bound_proposal_registry(path: Path, packet: R103ReviewPacket):
    try:
        proposal_registry = load_proposal_registry(path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise R103ReviewValidationError("invalid proposal registry") from error
    if (
        proposal_registry.registry_identity != packet.proposal_registry_identity
        or proposal_registry.ontology_version != packet.source_release
    ):
        raise R103ReviewValidationError(
            "proposal registry semantic binding differs from packet"
        )
    return proposal_registry


def _require_recomputed_dry_run(
    packet: R103ReviewPacket,
    registry: R103DecisionRegistry,
    persisted: R103ReviewDryRun,
    oracle_path: Path,
    proposal_registry_path: Path,
) -> None:
    recomputed = dry_run_r103_review(
        packet,
        registry,
        oracle_path=oracle_path,
        proposal_registry_path=proposal_registry_path,
    )
    if persisted != recomputed:
        raise R103ReviewValidationError("persisted dry-run differs from recomputation")


def _promoted_model(payload: dict[str, object]) -> R103PromotedReviewState:
    try:
        return R103PromotedReviewState.model_validate(
            {**payload, "artifact_identity": _identity(payload)}
        )
    except ValueError as error:
        raise R103ReviewValidationError(str(error)) from error


def _write_absent_or_equal(
    output_path: Path, promoted: R103PromotedReviewState
) -> None:
    content = _render_json(promoted.model_dump(mode="json"))
    if not output_path.exists():
        _write_json(output_path, promoted.model_dump(mode="json"))
        return
    try:
        existing = output_path.read_bytes()
    except OSError as error:
        raise R103ReviewValidationError("promotion output cannot be read") from error
    if existing != content:
        raise R103ReviewValidationError("promotion output conflict")


def promote_r103_review_state(
    *,
    packet_path: Path,
    registry_path: Path,
    dry_run_path: Path,
    oracle_path: Path,
    proposal_registry_path: Path,
    output_path: Path,
) -> R103PromotedReviewState:
    """Validate and compose the current incomplete, write-free review state."""
    packet = load_r103_review_packet(packet_path)
    registry = load_r103_decision_registry(registry_path)
    persisted_dry_run = _load_r103_review_dry_run(dry_run_path)
    proposal_registry = _load_bound_proposal_registry(proposal_registry_path, packet)
    oracle_sha256 = _file_sha256(oracle_path)
    proposal_file_sha256 = _file_sha256(proposal_registry_path)
    _require_recomputed_dry_run(
        packet,
        registry,
        persisted_dry_run,
        oracle_path=oracle_path,
        proposal_registry_path=proposal_registry_path,
    )
    payload: dict[str, object] = {
        "schema_version": 1,
        "packet": packet,
        "registry": registry,
        "dry_run": persisted_dry_run,
        "oracle_file_sha256": oracle_sha256,
        "proposal_registry_identity": proposal_registry.registry_identity,
        "proposal_registry_file_sha256": proposal_file_sha256,
    }
    promoted = _promoted_model(payload)
    _write_absent_or_equal(output_path, promoted)
    return promoted
