"""Owner #61 policy: every persisted stated occurrence has one visible category."""

import pytest

from ontolib.decomposition.enhancement_delta import DeltaOccurrence


@pytest.mark.parametrize(
    "role",
    [
        "R88",
        "R100",
        "R101",
        "R102",
        "R103",
        "R104",
        "R105",
        "R106",
        "R107",
        "R108",
        "R110",
        "R176",
        "R126",
        "R174",
    ],
)
@pytest.mark.parametrize(("depth", "bound"), [(1, 7), (7, 7), (8, 7), (7, 9)])
def test_dropped_axis_role_uses_only_recorded_depth_reason(role, depth, bound):
    row = DeltaOccurrence.model_validate(
        {
            "occurrence_id": "occ",
            "source_fact_id": "fact",
            "source_group_id": "group",
            "anchor_code": "C1",
            "depth": depth,
            "walker_max_depth": bound,
            "structural_path": [0],
            "role_code": role,
            "filler_code": "C2",
            "disposition": None,
            "normalized_axis": None,
            "retained_filler": None,
            "target_exists": False,
            "links": [],
            "conservation_category": None,
            "conservation_reason": None,
        }
    )
    assert row.category == "not-projected"
    assert row.reason == (
        "beyond the walker depth bound (D58)"
        if depth >= bound
        else "dropped by a projection rule (generic filler, held role or inherited "
        "non-core role); this engine version records no per-fact reason"
    )


def test_excludes_role_is_outside_axes_even_beyond_depth_bound():
    row = DeltaOccurrence.model_validate(
        {
            "occurrence_id": "occ",
            "source_fact_id": "fact",
            "source_group_id": "group",
            "anchor_code": "C1",
            "depth": 8,
            "walker_max_depth": 7,
            "structural_path": [0],
            "role_code": "R139",
            "filler_code": "C2",
            "disposition": None,
            "normalized_axis": None,
            "retained_filler": None,
            "target_exists": False,
            "links": [],
            "conservation_category": None,
            "conservation_reason": None,
        }
    )
    assert row.category == "not-considered"
    assert row.reason == "stated in NCIt; not part of the decomposition's axes"


@pytest.mark.parametrize(
    "kind", ["retained-routed", "retained-unknown", "retained-policy-veto"]
)
@pytest.mark.parametrize("role", ["R101", "R103", "R104", "R105"])
def test_retained_requires_exact_occurrence_link(kind: str, role: str) -> None:
    facts = {
        "occurrence_id": "occurrence",
        "source_fact_id": "fact",
        "source_group_id": "group",
        "anchor_code": "C1",
        "depth": 0,
        "walker_max_depth": 7,
        "structural_path": [0],
        "role_code": role,
        "filler_code": "C2",
        "disposition": kind,
        "normalized_axis": "op:PrimarySite",
        "retained_filler": "C3",
        "target_exists": True,
        "conservation_category": None,
        "conservation_reason": None,
    }
    for links, category in [
        ([{"axis": "op:PrimarySite", "filler_code": "C3"}], "projected"),
        ([{"axis": "op:Other", "filler_code": "C3"}], "not-projected"),
        ([{"axis": "op:PrimarySite", "filler_code": "C2"}], "not-projected"),
        ([], "not-projected"),
    ]:
        row = DeltaOccurrence.model_validate({**facts, "links": links})
        assert row.category == category
        assert row.reason == kind


@pytest.mark.parametrize("kind", ["collapsed-is-a", "collapsed-r82", "collapsed-mixed"])
@pytest.mark.parametrize("reason", [None, "missing-r82-path", "invalid-r82-path"])
def test_collapse_target_not_unresolved_label_decides_representation(
    kind, reason
) -> None:
    facts = {
        "occurrence_id": "occurrence",
        "source_fact_id": "fact",
        "source_group_id": "group",
        "anchor_code": "C1",
        "depth": 2,
        "walker_max_depth": 7,
        "structural_path": [0, 1],
        "role_code": "R101",
        "filler_code": "C2",
        "disposition": kind,
        "normalized_axis": "op:PrimarySite",
        "retained_filler": "C3",
        "links": [],
        "conservation_category": "unresolved",
        "conservation_reason": reason,
    }
    row = DeltaOccurrence.model_validate({**facts, "target_exists": True})
    assert row.category == "represented-through-collapse"
    assert row.retained_filler == "C3"
    assert row.reason == (reason or kind)
    absent = DeltaOccurrence.model_validate({**facts, "target_exists": False})
    assert absent.category == "not-projected"


@pytest.mark.parametrize(
    ("category", "reason", "links", "expected"),
    [
        ("unresolved", "missing-disposition", [], "not-projected"),
        ("unchanged-unprojected", "concept-not-decomposed", [], "not-projected"),
        (None, None, [], "not-projected"),
        (
            "unresolved",
            "missing-disposition",
            [{"axis": "op:PrimarySite", "filler_code": "C2"}],
            "unclassified",
        ),
        (None, None, [{"axis": "op:PrimarySite", "filler_code": "C2"}], "unclassified"),
        ("unresolved", "missing-r82-path", [], "unclassified"),
        ("projected", "retained-routed", [], "unclassified"),
    ],
)
def test_absent_disposition_does_not_hide_or_guess(
    category, reason, links, expected
) -> None:
    row = DeltaOccurrence(
        occurrence_id="occurrence",
        source_fact_id="fact",
        source_group_id="group",
        anchor_code="C1",
        depth=0,
        walker_max_depth=7,
        structural_path=[0],
        role_code="R101",
        filler_code="C2",
        disposition=None,
        normalized_axis=None,
        retained_filler=None,
        target_exists=False,
        links=links,
        conservation_category=category,
        conservation_reason=reason,
    )
    assert row.category == expected
    assert row.conservation_reason == reason
    if expected == "unclassified":
        assert row.reason == "unclassified"
    elif expected == "not-considered":
        assert row.reason == "stated in NCIt; not part of the decomposition's axes"
    elif reason is not None:
        assert row.reason == reason
