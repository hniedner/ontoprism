"""Range-only projection rejection and per-axis counting contracts."""

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
from ontolib.decomposition.projection_validity import freeze_projection_assessments

_SOURCE = "b58f48b5c19459c1273f3f4edf3fb67bd6f5e0e4c4d1c501218bf01b04ce6092"


def _range(axis: str, filler: str, status: str):
    range_code = AXIS_CONTRACTS[axis].range_code
    edges = {
        "valid": (HierarchyEdge(child=filler, parent=range_code),),
        "invalid": (HierarchyEdge(child=filler, parent="C43431"),),
        "unknown": (),
    }[status]
    return classify_axis_range(
        axis,
        filler,
        range_code,
        AxisHierarchyEvidence(
            source_identity=_SOURCE,
            edges=edges,
            disjoint_pairs=(DisjointPair(left=range_code, right="C43431"),)
            if status == "invalid"
            else (),
        ),
    )


def _plan(*rows, parent_morphologies=()):
    return build_routed_plan(
        rows,
        concept_code="C35756",
        source_identity=_SOURCE,
        collapse_policy=NO_COLLAPSE_VETO_POLICY,
        parent_morphologies=parent_morphologies,
    )


@pytest.mark.unit
def test_real_disjoint_proof_rejects_synthetic_occurrence_before_specificity():
    plan = _plan(
        RoleRestriction(
            "R88",
            "C141685",
            source_definition_ids=("1" * 64,),
            source_occurrence_ids=("2" * 64,),
        ),
        RoleRestriction(
            "R88",
            "C90530",
            source_definition_ids=("3" * 64,),
            source_occurrence_ids=("4" * 64,),
        ),
    )
    comparisons = []
    result = select_assessed_routed_plan(
        plan,
        lambda a, b: not comparisons.append((a, b)),
        assessments=freeze_projection_assessments(
            (
                _range("op:StageSystem", "C141685", "invalid"),
                _range("op:StageSystem", "C90530", "valid"),
            )
        ),
    )
    assert [(c.axis, c.filler_code) for c in result.constituents] == [
        ("op:StageSystem", "C90530")
    ]
    assert comparisons == []
    assert result.invalid_axis_range_by_axis == {"op:StageSystem": 1}
    assert {row.source_occurrence_id for row in result.dispositions} == {"4" * 64}


@pytest.mark.unit
def test_rejection_counts_unique_pairs_including_parent_morphologies():
    plan = _plan(
        RoleRestriction("R88", "C10", source_kind="synthetic"),
        RoleRestriction("R88", "C10", source_kind="synthetic"),
        RoleRestriction("R88", "C20", source_kind="synthetic"),
        RoleRestriction("R105", "C30", source_kind="synthetic"),
        parent_morphologies=("C40",),
    )
    result = select_assessed_routed_plan(
        plan,
        lambda a, b: False,
        assessments=freeze_projection_assessments(
            (
                _range("op:StageValue", "C10", "invalid"),
                _range("op:StageValue", "C20", "invalid"),
                _range("op:CellType", "C30", "unknown"),
                _range("op:Morphology", "C40", "invalid"),
            )
        ),
    )
    assert result.invalid_axis_range_by_axis == {"op:StageValue": 2, "op:Morphology": 1}
    assert [(c.axis, c.filler_code) for c in result.constituents] == [
        ("op:CellType", "C30")
    ]


@pytest.mark.unit
@pytest.mark.parametrize("status", ["valid", "unknown"])
def test_surviving_ranges_have_zero_rejections(status):
    result = select_assessed_routed_plan(
        _plan(RoleRestriction("R88", "C10", source_kind="synthetic")),
        lambda a, b: False,
        assessments=freeze_projection_assessments(
            (_range("op:StageValue", "C10", status),)
        ),
    )
    assert [c.filler_code for c in result.constituents] == ["C10"]
    assert result.invalid_axis_range_by_axis == {}


@pytest.mark.unit
def test_missing_extra_and_miskeyed_range_evidence_fails_closed():
    plan = _plan(RoleRestriction("R88", "C10", source_kind="synthetic"))
    evidence = _range("op:StageValue", "C10", "valid")
    for assessments, message in [
        ({}, "missing"),
        (
            freeze_projection_assessments(
                (evidence, _range("op:StageValue", "C20", "valid"))
            ),
            "extraneous",
        ),
        (
            {("op:StageValue", "C10"): _range("op:StageValue", "C20", "valid")},
            "key differs",
        ),
    ]:
        with pytest.raises(ValueError, match=message):
            select_assessed_routed_plan(
                plan, lambda a, b: False, assessments=assessments
            )


@pytest.mark.unit
def test_specificity_reduction_only_compares_projection_survivors():
    plan = _plan(
        *(
            RoleRestriction("R105", filler, source_kind="synthetic")
            for filler in ("C10", "C20", "C30")
        )
    )
    compared = set()
    result = select_assessed_routed_plan(
        plan,
        lambda a, b: not compared.add((a, b)) and (a, b) == ("C20", "C30"),
        assessments=freeze_projection_assessments(
            (
                _range("op:CellType", "C10", "invalid"),
                _range("op:CellType", "C20", "valid"),
                _range("op:CellType", "C30", "valid"),
            )
        ),
    )
    assert all("C10" not in pair for pair in compared)
    assert {c.filler_code for c in result.constituents} == {"C30"}


@pytest.mark.unit
def test_assessment_map_is_closed_unique_and_immutable():
    evidence = _range("op:StageSystem", "C10", "valid")
    frozen = freeze_projection_assessments((evidence,))
    assert frozen[("op:StageSystem", "C10")] is evidence
    with pytest.raises(TypeError):
        frozen[("op:StageSystem", "C20")] = evidence
    with pytest.raises(ValueError, match="duplicate"):
        freeze_projection_assessments((evidence, evidence))
