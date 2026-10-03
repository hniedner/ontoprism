"""Projection-validity decision contracts for issue #271."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from ontolib.decomposition.axis_contracts import AXIS_CONTRACTS
from ontolib.decomposition.axis_diagnostics import (
    AxisHierarchyEvidence,
    DisjointPair,
    HierarchyEdge,
    classify_axis_range,
)
from ontolib.decomposition.collapse_policy import NO_COLLAPSE_VETO_POLICY
from ontolib.decomposition.filler_selection import (
    build_routed_plan,
    select_assessed_routed_plan,
)
from ontolib.decomposition.models import RoleRestriction
from ontolib.decomposition.projection_validity import (
    AtomicProjectionEvidence,
    ProjectionAssessment,
    ProjectionDecision,
    ResidualProjectionEvidence,
    UnknownProjectionEvidence,
    decide_projection,
    freeze_projection_assessments,
)

_SOURCE = "b58f48b5c19459c1273f3f4edf3fb67bd6f5e0e4c4d1c501218bf01b04ce6092"
_DETECTOR = "d" * 64


def _range(axis: str, filler: str, *, status: str):
    range_code = AXIS_CONTRACTS[axis].range_code
    if status == "valid":
        edges = (HierarchyEdge(child=filler, parent=range_code),)
    elif status == "invalid":
        edges = (HierarchyEdge(child=filler, parent="C43431"),)
    else:
        edges = ()
    disjoint = (
        (DisjointPair(left=range_code, right="C43431"),) if status == "invalid" else ()
    )
    return classify_axis_range(
        axis,
        filler,
        range_code,
        AxisHierarchyEvidence(
            source_identity=_SOURCE,
            edges=edges,
            disjoint_pairs=disjoint,
        ),
    )


def _assessment(axis: str, filler: str, range_status: str, atomicity: str):
    atomicity_evidence = {
        "atomic": AtomicProjectionEvidence(
            status="atomic",
            reason="production-detector",
            filler_code=filler,
            detector_identity=_DETECTOR,
        ),
        "residual": ResidualProjectionEvidence(
            status="residual",
            reason="production-detector",
            filler_code=filler,
            detector_identity=_DETECTOR,
        ),
        "unknown": UnknownProjectionEvidence(
            status="unknown",
            reason="unsupported-definition-constructor",
            filler_code=filler,
            detector_identity=_DETECTOR,
        ),
    }[atomicity]
    return ProjectionAssessment(
        axis_range=_range(axis, filler, status=range_status),
        atomicity=atomicity_evidence,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("range_status", "atomicity", "outcome", "review_bearing"),
    [
        ("valid", "atomic", "accepted", False),
        ("valid", "residual", "accepted", True),
        ("valid", "unknown", "accepted", True),
        ("unknown", "atomic", "accepted", True),
        ("unknown", "unknown", "accepted", True),
        ("invalid", "atomic", "rejected", False),
        ("invalid", "residual", "rejected", False),
        ("invalid", "unknown", "rejected", False),
    ],
)
def test_axis_range_and_atomicity_drive_independent_typed_decisions(
    range_status: str,
    atomicity: str,
    outcome: str,
    review_bearing: bool,
) -> None:
    assessment = _assessment("op:StageSystem", "C141685", range_status, atomicity)

    decision = decide_projection(assessment)

    assert decision.outcome == outcome
    assert decision.review_bearing is review_bearing
    assert decision.axis_range_status == range_status
    assert decision.atomicity_status == atomicity


@pytest.mark.unit
def test_assessment_rejects_cross_filler_evidence_and_is_immutable() -> None:
    with pytest.raises(ValueError, match="same filler"):
        ProjectionAssessment(
            axis_range=_range("op:StageSystem", "C141685", status="valid"),
            atomicity=AtomicProjectionEvidence(
                status="atomic",
                reason="production-detector",
                filler_code="C90530",
                detector_identity=_DETECTOR,
            ),
        )

    assessment = _assessment("op:StageSystem", "C141685", "valid", "atomic")
    with pytest.raises(FrozenInstanceError):
        assessment.atomicity = assessment.atomicity  # type: ignore[misc]


@pytest.mark.unit
def test_atomicity_evidence_rejects_impossible_identity_and_reason_states() -> None:
    with pytest.raises(ValueError, match="filler code"):
        AtomicProjectionEvidence(
            status="atomic",
            reason="production-detector",
            filler_code="unsafe",
            detector_identity=_DETECTOR,
        )
    with pytest.raises(ValueError, match="detector identity"):
        AtomicProjectionEvidence(
            status="atomic",
            reason="production-detector",
            filler_code="C141685",
            detector_identity="invalid",
        )
    with pytest.raises(ValueError, match="production detector"):
        ResidualProjectionEvidence(
            status="residual",
            reason="unsupported-definition-constructor",
            filler_code="C141685",
            detector_identity=_DETECTOR,
        )
    with pytest.raises(ValueError, match="uncertainty reason"):
        UnknownProjectionEvidence(
            status="unknown",
            reason="production-detector",
            filler_code="C141685",
            detector_identity=_DETECTOR,
        )
    with pytest.raises(ValueError, match="status must be"):
        AtomicProjectionEvidence(
            status="unknown",  # type: ignore[arg-type]
            reason="production-detector",
            filler_code="C141685",
            detector_identity=_DETECTOR,
        )


@pytest.mark.unit
def test_projection_decision_rejects_internally_contradictory_states() -> None:
    with pytest.raises(ValueError, match="only invalid axis range"):
        ProjectionDecision(
            axis="op:StageSystem",
            filler_code="C141685",
            outcome="accepted",
            review_bearing=True,
            axis_range_status="invalid",
            atomicity_status="unknown",
            reasons=("atomicity-unknown",),
        )
    with pytest.raises(ValueError, match="impossible review semantics"):
        ProjectionDecision(
            axis="op:StageSystem",
            filler_code="C141685",
            outcome="rejected",
            review_bearing=True,
            axis_range_status="invalid",
            atomicity_status="unknown",
            reasons=("invalid-axis-range",),
        )
    with pytest.raises(ValueError, match="review flag"):
        ProjectionDecision(
            axis="op:StageSystem",
            filler_code="C90530",
            outcome="accepted",
            review_bearing=True,
            axis_range_status="valid",
            atomicity_status="atomic",
            reasons=("valid-atomic",),
        )


@pytest.mark.unit
def test_missing_assessment_fails_closed_after_final_route() -> None:
    plan = build_routed_plan(
        (RoleRestriction("R88", "C141685", source_kind="synthetic"),),
        concept_code="C35756",
        source_identity=_SOURCE,
        collapse_policy=NO_COLLAPSE_VETO_POLICY,
    )

    with pytest.raises(ValueError, match=r"missing.*op:StageSystem.*C141685"):
        select_assessed_routed_plan(
            plan,
            lambda _broader, _narrower: False,
            assessments=freeze_projection_assessments(()),
        )
    with pytest.raises(ValueError, match=r"extraneous.*op:StageSystem.*C90530"):
        select_assessed_routed_plan(
            plan,
            lambda _broader, _narrower: False,
            assessments=freeze_projection_assessments(
                (
                    _assessment("op:StageSystem", "C141685", "valid", "atomic"),
                    _assessment("op:StageSystem", "C90530", "valid", "atomic"),
                )
            ),
        )


@pytest.mark.unit
def test_real_disjoint_proof_rejects_synthetic_occurrence_before_specificity() -> None:
    """Use fixture liveness, not a claimed C35756 occurrence or current emission."""
    invalid_occurrence = RoleRestriction(
        "R88",
        "C141685",
        source_definition_ids=("1" * 64,),
        source_occurrence_ids=("2" * 64,),
    )
    valid_occurrence = RoleRestriction(
        "R88",
        "C90530",
        source_definition_ids=("3" * 64,),
        source_occurrence_ids=("4" * 64,),
    )
    plan = build_routed_plan(
        (invalid_occurrence, valid_occurrence),
        concept_code="C35756",
        source_identity=_SOURCE,
        collapse_policy=NO_COLLAPSE_VETO_POLICY,
    )
    assessments = freeze_projection_assessments(
        (
            _assessment("op:StageSystem", "C141685", "invalid", "atomic"),
            _assessment("op:StageSystem", "C90530", "valid", "atomic"),
        )
    )
    comparisons: list[tuple[str, str]] = []

    result = select_assessed_routed_plan(
        plan,
        lambda broader, narrower: not comparisons.append((broader, narrower)),
        assessments=assessments,
    )

    assert [(row.axis, row.filler_code) for row in result.constituents] == [
        ("op:StageSystem", "C90530")
    ]
    assert comparisons == []
    assert result.projection_decisions[0].axis == "op:StageSystem"
    assert result.projection_decisions[0].filler_code == "C141685"
    assert result.projection_decisions[0].outcome == "rejected"
    assert result.projection_decisions[0].source_occurrence_ids == ("2" * 64,)
    assert {row.source_occurrence_id for row in result.dispositions} == {"4" * 64}


@pytest.mark.unit
def test_specificity_reduction_only_compares_projection_survivors() -> None:
    rows = tuple(
        RoleRestriction(
            "R105",
            filler,
            source_definition_ids=(str(index) * 64,),
            source_occurrence_ids=(chr(96 + index) * 64,),
        )
        for index, filler in enumerate(("C10", "C20", "C30"), start=1)
    )
    plan = build_routed_plan(
        rows,
        concept_code="C9000",
        source_identity=_SOURCE,
        collapse_policy=NO_COLLAPSE_VETO_POLICY,
    )
    assessments = freeze_projection_assessments(
        (
            _assessment("op:CellType", "C10", "invalid", "atomic"),
            _assessment("op:CellType", "C20", "valid", "atomic"),
            _assessment("op:CellType", "C30", "valid", "atomic"),
        )
    )
    compared: set[tuple[str, str]] = set()

    result = select_assessed_routed_plan(
        plan,
        lambda broader, narrower: (
            not compared.add((broader, narrower))
            and (broader, narrower) == ("C20", "C30")
        ),
        assessments=assessments,
    )

    assert all("C10" not in pair for pair in compared)
    assert {row.filler_code for row in result.constituents} == {"C30"}


@pytest.mark.unit
def test_assessment_map_is_closed_unique_and_immutable() -> None:
    assessment = _assessment("op:StageSystem", "C90530", "valid", "atomic")
    frozen = freeze_projection_assessments((assessment,))
    assert frozen[("op:StageSystem", "C90530")] is assessment
    with pytest.raises(TypeError):
        frozen[("op:StageSystem", "C141685")] = assessment  # type: ignore[index]
    with pytest.raises(ValueError, match="duplicate"):
        freeze_projection_assessments((assessment, assessment))
