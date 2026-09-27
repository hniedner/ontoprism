"""Owner #61 policy: every persisted stated occurrence has one visible category."""

import pytest

from ontolib.decomposition.enhancement_delta import DeltaOccurrence


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
        (None, None, [], "not-considered"),
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
    else:
        assert row.reason == reason
