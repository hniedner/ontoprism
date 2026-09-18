"""Occurrence-level route-before-collapse contracts for issue #267."""

from __future__ import annotations

import pytest

from ontolib.decomposition.collapse_policy import (
    NO_COLLAPSE_VETO_POLICY,
    CollapsePolicyError,
    CollapseVeto,
    CollapseVetoPolicy,
)
from ontolib.decomposition.filler_selection import (
    _reduce_routed_plan,
    build_routed_plan,
)
from ontolib.decomposition.models import RoleRestriction

_SOURCE = "a" * 64


def _restriction(
    filler: str,
    *,
    role: str = "R101",
    anchor: str = "C9000",
    fact: str,
    occurrence: str,
) -> RoleRestriction:
    return RoleRestriction(
        role,
        filler,
        anchoring_genus=anchor,
        source_definition_ids=(fact,),
        source_occurrence_ids=(occurrence,),
    )


@pytest.mark.unit
def test_routed_occurrence_plan_drives_query_groups_and_r82_dispositions() -> None:
    rows = (
        _restriction("C12400", fact="1" * 64, occurrence="a" * 64),
        _restriction("C12418", fact="2" * 64, occurrence="b" * 64),
        _restriction("C13063", fact="3" * 64, occurrence="c" * 64),
        _restriction(
            "C12705",
            anchor="C3010",
            fact="4" * 64,
            occurrence="d" * 64,
        ),
    )
    semantic_types = {
        "C12400": "Body Part, Organ, or Organ Component",
        "C12418": "Anatomical Structure",
        "C13063": "Anatomical Structure",
        "C12705": "Body System",
    }

    plan = build_routed_plan(
        rows,
        semantic_type_of=semantic_types.get,
        concept_code="C6135",
        source_identity=_SOURCE,
        collapse_policy=NO_COLLAPSE_VETO_POLICY,
    )

    assert plan.comparison_groups == (("op:AssociatedRegion", ("C12418", "C13063")),)
    assert plan.specificity_groups == plan.comparison_groups
    result = _reduce_routed_plan(
        plan,
        lambda _broader, _narrower: False,
        is_part_of=lambda part, whole: (part, whole) == ("C13063", "C12418"),
    )
    assert [
        (
            row.axis,
            row.filler_code,
            row.axis_ambiguity_group_id,
            row.needs_review,
            row.most_specific,
            row.source_occurrence_ids,
        )
        for row in result.constituents
    ] == [
        (
            "op:AssociatedLineageClassification",
            "C12705",
            None,
            False,
            False,
            ("d" * 64,),
        ),
        ("op:AssociatedRegion", "C13063", None, False, True, ("c" * 64,)),
        ("op:PrimarySite", "C12400", None, False, False, ("a" * 64,)),
    ]
    by_occurrence = {row.source_occurrence_id: row for row in result.dispositions}
    assert by_occurrence["a" * 64].kind == "retained-routed"
    collapsed = by_occurrence["b" * 64]
    assert (
        collapsed.kind,
        collapsed.normalized_axis,
        collapsed.retained_filler,
        collapsed.r82_part,
        collapsed.r82_whole,
        collapsed.source_fact_id,
    ) == (
        "collapsed-r82",
        "op:AssociatedRegion",
        "C13063",
        "C13063",
        "C12418",
        "2" * 64,
    )
    assert by_occurrence["c" * 64].kind == "retained-routed"
    assert by_occurrence["d" * 64].kind == "retained-routed"


@pytest.mark.unit
def test_unknown_semantics_and_unknown_roles_are_never_specificity_collapsed() -> None:
    rows = (
        _restriction("C1", fact="1" * 64, occurrence="1" * 64),
        _restriction("C2", fact="2" * 64, occurrence="2" * 64),
        _restriction("C3", role="R999", fact="3" * 64, occurrence="3" * 64),
        _restriction("C4", role="R999", fact="4" * 64, occurrence="4" * 64),
    )
    plan = build_routed_plan(
        rows,
        semantic_type_of=lambda _code: None,
        concept_code="C9000",
        source_identity=_SOURCE,
        collapse_policy=NO_COLLAPSE_VETO_POLICY,
    )

    assert plan.comparison_groups == ()
    result = _reduce_routed_plan(
        plan,
        lambda broader, narrower: (broader, narrower) in {("C1", "C2"), ("C3", "C4")},
        is_part_of=lambda part, whole: (part, whole) == ("C2", "C1"),
    )
    assert {
        (row.axis, row.filler_code, row.needs_review) for row in result.constituents
    } == {
        ("op:PrimarySite", "C1", True),
        ("op:PrimarySite", "C2", True),
        ("R999", "C3", True),
        ("R999", "C4", True),
    }
    assert {row.kind for row in result.dispositions} == {"retained-unknown"}


@pytest.mark.unit
def test_unknown_r101_does_not_block_collapse_inside_known_region_partition() -> None:
    rows = (
        _restriction("C1", fact="1" * 64, occurrence="1" * 64),
        _restriction("C2", fact="2" * 64, occurrence="2" * 64),
        _restriction("C3", fact="3" * 64, occurrence="3" * 64),
    )
    semantic_types = {"C1": "Anatomical Structure", "C2": "Anatomical Structure"}
    plan = build_routed_plan(
        rows,
        semantic_type_of=semantic_types.get,
        concept_code="C9000",
        source_identity=_SOURCE,
        collapse_policy=NO_COLLAPSE_VETO_POLICY,
    )
    assert plan.specificity_groups == (("op:AssociatedRegion", ("C1", "C2")),)
    assert plan.comparison_groups == plan.specificity_groups

    result = _reduce_routed_plan(
        plan,
        lambda _broader, _narrower: False,
        is_part_of=lambda part, whole: (part, whole) == ("C2", "C1"),
    )

    assert {(row.axis, row.filler_code) for row in result.constituents} == {
        ("op:AssociatedRegion", "C2"),
        ("op:PrimarySite", "C3"),
    }
    by_occurrence = {row.source_occurrence_id: row for row in result.dispositions}
    assert by_occurrence["1" * 64].kind == "collapsed-r82"
    assert by_occurrence["3" * 64].kind == "retained-unknown"


@pytest.mark.unit
def test_three_node_specificity_cycle_fails_closed() -> None:
    rows = tuple(
        _restriction(f"C{index}", fact=str(index) * 64, occurrence=str(index) * 64)
        for index in (1, 2, 3)
    )
    plan = build_routed_plan(
        rows,
        semantic_type_of=lambda _code: "Anatomical Structure",
        concept_code="C9000",
        source_identity=_SOURCE,
        collapse_policy=NO_COLLAPSE_VETO_POLICY,
    )

    with pytest.raises(ValueError, match="cycle"):
        _reduce_routed_plan(
            plan,
            lambda broader, narrower: (
                (broader, narrower) in {("C1", "C2"), ("C2", "C3"), ("C3", "C1")}
            ),
        )


@pytest.mark.unit
def test_transitive_collapse_disposition_names_the_surviving_leaf() -> None:
    rows = tuple(
        _restriction(code, fact=str(index) * 64, occurrence=str(index) * 64)
        for index, code in enumerate(("C2", "C1", "C9"), start=1)
    )
    plan = build_routed_plan(
        rows,
        semantic_type_of=lambda _code: "Anatomical Structure",
        concept_code="C9000",
        source_identity=_SOURCE,
        collapse_policy=NO_COLLAPSE_VETO_POLICY,
    )
    result = _reduce_routed_plan(
        plan,
        lambda broader, narrower: (
            (broader, narrower) in {("C2", "C1"), ("C1", "C9"), ("C2", "C9")}
        ),
    )

    assert {row.filler_code for row in result.constituents} == {"C9"}
    broad = next(row for row in result.dispositions if row.source_filler == "C2")
    assert broad.retained_filler == "C9"


@pytest.mark.unit
def test_mixed_chain_collapses_to_terminal_with_truthful_path() -> None:
    rows = tuple(
        _restriction(code, role="R100", fact=str(index) * 64, occurrence=letter * 64)
        for index, (code, letter) in enumerate(
            (("C1", "a"), ("C2", "b"), ("C3", "c")), start=1
        )
    )
    plan = build_routed_plan(
        rows,
        concept_code="C9000",
        source_identity=_SOURCE,
        collapse_policy=NO_COLLAPSE_VETO_POLICY,
    )

    result = _reduce_routed_plan(
        plan,
        lambda broader, narrower: (broader, narrower) == ("C1", "C2"),
        is_part_of=lambda part, whole: (part, whole) == ("C3", "C2"),
    )

    assert [
        (row.axis, row.filler_code, row.most_specific) for row in result.constituents
    ] == [("op:AssociatedSite", "C3", True)]
    by_occurrence = {row.source_occurrence_id: row for row in result.dispositions}
    broad = by_occurrence["a" * 64]
    assert (
        broad.kind,
        broad.retained_filler,
        broad.source_fact_id,
        broad.source_occurrence_id,
    ) == ("collapsed-mixed", "C3", "1" * 64, "a" * 64)
    assert [
        (edge.kind, edge.broader_code, edge.narrower_code, edge.source_identity)
        for edge in broad.specificity_path
    ] == [
        ("is-a", "C1", "C2", _SOURCE),
        ("r82", "C2", "C3", _SOURCE),
    ]
    assert by_occurrence["b" * 64].kind == "collapsed-r82"
    assert by_occurrence["c" * 64].kind == "retained-routed"


@pytest.mark.unit
def test_known_nonexempt_ambiguity_has_axis_bound_review_group() -> None:
    plan = build_routed_plan(
        (
            _restriction("C1", role="R105", fact="1" * 64, occurrence="1" * 64),
            _restriction("C2", role="R105", fact="2" * 64, occurrence="2" * 64),
        ),
        concept_code="C9000",
        source_identity=_SOURCE,
        collapse_policy=NO_COLLAPSE_VETO_POLICY,
    )

    result = _reduce_routed_plan(plan, lambda _broader, _narrower: False)

    assert {
        (row.needs_review, row.axis_ambiguity_group_id) for row in result.constituents
    } == {(True, "op:CellType")}


@pytest.mark.unit
def test_r82_reverse_cross_axis_and_nonlocation_relations_do_not_collapse() -> None:
    rows = (
        _restriction("C1", fact="1" * 64, occurrence="1" * 64),
        _restriction("C2", fact="2" * 64, occurrence="2" * 64),
        _restriction("C3", anchor="C3010", fact="3" * 64, occurrence="3" * 64),
        _restriction("C4", role="R105", fact="4" * 64, occurrence="4" * 64),
        _restriction("C5", role="R105", fact="5" * 64, occurrence="5" * 64),
    )
    semantic_types = {
        "C1": "Body Part, Organ, or Organ Component",
        "C2": "Anatomical Structure",
        "C3": "Anatomical Structure",
    }
    plan = build_routed_plan(
        rows,
        semantic_type_of=semantic_types.get,
        concept_code="C9000",
        source_identity=_SOURCE,
        collapse_policy=NO_COLLAPSE_VETO_POLICY,
    )
    assert plan.specificity_groups == (("op:CellType", ("C4", "C5")),)
    assert plan.comparison_groups == ()

    result = _reduce_routed_plan(
        plan,
        lambda _broader, _narrower: False,
        is_part_of=lambda part, whole: (
            (part, whole)
            in {
                ("C1", "C2"),  # reverse direction for C2 -> C1
                ("C2", "C3"),  # cross final axes
                ("C5", "C4"),  # non-location axis
            }
        ),
    )

    assert {row.source_filler for row in result.dispositions} == {
        "C1",
        "C2",
        "C3",
        "C4",
        "C5",
    }
    assert all(row.kind == "retained-routed" for row in result.dispositions)


@pytest.mark.unit
def test_mutual_broader_relation_fails_closed_in_eligible_partition() -> None:
    rows = (
        _restriction("C1", fact="1" * 64, occurrence="1" * 64),
        _restriction("C2", fact="2" * 64, occurrence="2" * 64),
    )
    plan = build_routed_plan(
        rows,
        semantic_type_of=lambda _code: "Anatomical Structure",
        concept_code="C9000",
        source_identity=_SOURCE,
        collapse_policy=NO_COLLAPSE_VETO_POLICY,
    )

    with pytest.raises(ValueError, match=r"cycle|mutually broader"):
        _reduce_routed_plan(
            plan,
            lambda _broader, _narrower: False,
            is_part_of=lambda part, whole: (
                part != whole and {part, whole} == {"C1", "C2"}
            ),
        )


@pytest.mark.unit
def test_live_veto_axis_drift_fails_closed_in_routed_plan() -> None:
    broader_occurrence = "1" * 64
    policy = CollapseVetoPolicy.create(
        registry_identity="2" * 64,
        entries=(
            CollapseVeto(
                source_identity=_SOURCE,
                concept_code="C9000",
                role_code="R101",
                anchoring_genus="C9000",
                normalized_axis="op:PrimarySite",
                broader_code="C1",
                narrower_code="C2",
                occurrence_id=broader_occurrence,
                atomic_decision_identity="3" * 64,
            ),
        ),
    )
    rows = (
        _restriction("C1", fact="1" * 64, occurrence=broader_occurrence),
        _restriction("C2", fact="2" * 64, occurrence="4" * 64),
    )

    with pytest.raises(CollapsePolicyError, match=r"axis.*drift"):
        build_routed_plan(
            rows,
            semantic_type_of=lambda _code: "Anatomical Structure",
            concept_code="C9000",
            source_identity=_SOURCE,
            collapse_policy=policy,
        )


@pytest.mark.unit
def test_live_veto_retention_carries_exact_policy_decision_evidence() -> None:
    broader_occurrence = "1" * 64
    decision_identity = "3" * 64
    policy = CollapseVetoPolicy.create(
        registry_identity="2" * 64,
        entries=(
            CollapseVeto(
                source_identity=_SOURCE,
                concept_code="C9000",
                role_code="R101",
                anchoring_genus="C9000",
                normalized_axis="op:AssociatedRegion",
                broader_code="C1",
                narrower_code="C2",
                occurrence_id=broader_occurrence,
                atomic_decision_identity=decision_identity,
            ),
        ),
    )
    plan = build_routed_plan(
        (
            _restriction("C1", fact="1" * 64, occurrence=broader_occurrence),
            _restriction("C2", fact="2" * 64, occurrence="4" * 64),
        ),
        semantic_type_of=lambda _code: "Anatomical Structure",
        concept_code="C9000",
        source_identity=_SOURCE,
        collapse_policy=policy,
    )

    result = _reduce_routed_plan(
        plan,
        lambda broader, narrower: (broader, narrower) == ("C1", "C2"),
    )

    assert {row.filler_code for row in result.constituents} == {"C1", "C2"}
    disposition = next(
        row
        for row in result.dispositions
        if row.source_occurrence_id == broader_occurrence
    )
    assert disposition.kind == "retained-policy-veto"
    assert disposition.policy_decision_identity == decision_identity
