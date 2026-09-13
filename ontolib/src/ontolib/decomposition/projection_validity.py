"""Typed, source-bound projection validity and atomicity decisions.

Axis range and residual precoordination are deliberately independent evidence
planes.  This module only decides whether a curated projection may survive; it
never changes the complete stated definition that supplied the candidate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

from ontolib.decomposition.axis_diagnostics import (
    AxisRangeEvidence,
    InvalidAxisEvidence,
    UnknownAxisEvidence,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

_CODE = re.compile(r"C[0-9]+")
_SHA256 = re.compile(r"[0-9a-f]{64}")

AtomicityReason = Literal[
    "production-detector",
    "unsupported-definition-constructor",
    "proposed-filler-not-in-source",
    "not-classified-for-issue-replay",
]


def _validate_atomicity_identity(filler_code: str, detector_identity: str) -> None:
    if _CODE.fullmatch(filler_code) is None and not filler_code.startswith("MINT-"):
        raise ValueError("atomicity filler code is invalid")
    if _SHA256.fullmatch(detector_identity) is None:
        raise ValueError("atomicity detector identity is invalid")


def _validate_atomicity_reason(expected: str, reason: AtomicityReason) -> None:
    if expected in {"atomic", "residual"} and reason != "production-detector":
        raise ValueError(f"{expected} atomicity requires production detector evidence")
    if expected == "unknown" and reason == "production-detector":
        raise ValueError("unknown atomicity requires an uncertainty reason")


def _validate_atomicity(
    status: object,
    expected: str,
    reason: AtomicityReason,
    filler_code: str,
    detector_identity: str,
) -> None:
    if status != expected:
        raise ValueError(f"atomicity status must be {expected!r}")
    _validate_atomicity_identity(filler_code, detector_identity)
    _validate_atomicity_reason(expected, reason)


@dataclass(frozen=True, slots=True, kw_only=True)
class AtomicProjectionEvidence:
    status: Literal["atomic"]
    reason: AtomicityReason
    filler_code: str
    detector_identity: str

    def __post_init__(self) -> None:
        _validate_atomicity(
            self.status,
            "atomic",
            self.reason,
            self.filler_code,
            self.detector_identity,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ResidualProjectionEvidence:
    status: Literal["residual"]
    reason: AtomicityReason
    filler_code: str
    detector_identity: str

    def __post_init__(self) -> None:
        _validate_atomicity(
            self.status,
            "residual",
            self.reason,
            self.filler_code,
            self.detector_identity,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class UnknownProjectionEvidence:
    status: Literal["unknown"]
    reason: AtomicityReason
    filler_code: str
    detector_identity: str

    def __post_init__(self) -> None:
        _validate_atomicity(
            self.status,
            "unknown",
            self.reason,
            self.filler_code,
            self.detector_identity,
        )


type ProjectionAtomicityEvidence = (
    AtomicProjectionEvidence | ResidualProjectionEvidence | UnknownProjectionEvidence
)


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectionAssessment:
    """One complete `(axis, filler)` assessment across both evidence planes."""

    axis_range: AxisRangeEvidence
    atomicity: ProjectionAtomicityEvidence

    def __post_init__(self) -> None:
        if self.axis_range.filler_code != self.atomicity.filler_code:
            raise ValueError("axis range and atomicity must assess the same filler")

    @property
    def key(self) -> tuple[str, str]:
        return self.axis_range.axis, self.axis_range.filler_code


ProjectionOutcome = Literal["accepted", "rejected"]
ProjectionDecisionReason = Literal[
    "valid-atomic",
    "residual-precoordination",
    "axis-range-unknown",
    "atomicity-unknown",
    "invalid-axis-range",
]


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectionDecision:
    axis: str
    filler_code: str
    outcome: ProjectionOutcome
    review_bearing: bool
    axis_range_status: Literal["valid", "invalid", "unknown"]
    atomicity_status: Literal["atomic", "residual", "unknown"]
    reasons: tuple[ProjectionDecisionReason, ...]

    def __post_init__(self) -> None:
        _validate_projection_outcome(self)
        _validate_projection_review(self)


def _validate_projection_outcome(decision: ProjectionDecision) -> None:
    rejected = decision.outcome == "rejected"
    if rejected != (decision.axis_range_status == "invalid"):
        raise ValueError("only invalid axis range may reject a projection")
    if rejected and (
        decision.review_bearing or decision.reasons != ("invalid-axis-range",)
    ):
        raise ValueError("invalid projection decision has impossible review semantics")


def _validate_projection_review(decision: ProjectionDecision) -> None:
    expected_review = decision.outcome != "rejected" and (
        decision.axis_range_status == "unknown" or decision.atomicity_status != "atomic"
    )
    if decision.review_bearing != expected_review:
        raise ValueError("projection review flag differs from evidence")


def decide_projection(assessment: ProjectionAssessment) -> ProjectionDecision:
    """Reject invalid range only; retain every uncertainty as review-bearing."""
    axis_range = assessment.axis_range
    atomicity = assessment.atomicity
    if isinstance(axis_range, InvalidAxisEvidence):
        return ProjectionDecision(
            axis=axis_range.axis,
            filler_code=axis_range.filler_code,
            outcome="rejected",
            review_bearing=False,
            axis_range_status="invalid",
            atomicity_status=atomicity.status,
            reasons=("invalid-axis-range",),
        )
    reasons: list[ProjectionDecisionReason] = []
    if isinstance(axis_range, UnknownAxisEvidence):
        reasons.append("axis-range-unknown")
    if atomicity.status == "residual":
        reasons.append("residual-precoordination")
    elif atomicity.status == "unknown":
        reasons.append("atomicity-unknown")
    if not reasons:
        reasons.append("valid-atomic")
    return ProjectionDecision(
        axis=axis_range.axis,
        filler_code=axis_range.filler_code,
        outcome="accepted",
        review_bearing=(axis_range.status == "unknown" or atomicity.status != "atomic"),
        axis_range_status=axis_range.status,
        atomicity_status=atomicity.status,
        reasons=tuple(reasons),
    )


def freeze_projection_assessments(
    assessments: Iterable[ProjectionAssessment],
) -> Mapping[tuple[str, str], ProjectionAssessment]:
    """Build a duplicate-rejecting immutable assessment map."""
    result: dict[tuple[str, str], ProjectionAssessment] = {}
    for assessment in assessments:
        if assessment.key in result:
            raise ValueError(f"duplicate projection assessment: {assessment.key!r}")
        result[assessment.key] = assessment
    return MappingProxyType(result)
