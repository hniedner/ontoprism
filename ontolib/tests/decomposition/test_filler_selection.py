"""Unit tests for filler selection: Excludes filter, most-specific, morphology."""

from collections import defaultdict

import pytest

from ontolib.decomposition.axes import (
    ASSOCIATED_LINEAGE_AXIS,
    ASSOCIATED_REGION_AXIS,
    MORPHOLOGY_AXIS,
)
from ontolib.decomposition.filler_selection import (
    filter_excluded,
    most_specific,
    route_axis,
    select_constituents,
)
from ontolib.decomposition.models import RoleRestriction

# A tiny fake hierarchy: (ancestor, descendant) pairs. Endocrine Gland and Neck are
# both ancestors of Thyroid Gland (the inferred-graph ancestor bleed, assessment §4).
_ANCESTORS = {
    ("C12401", "C12400"),  # Endocrine Gland -> Thyroid Gland
    ("C12402", "C12400"),  # Neck -> Thyroid Gland
    ("C12403", "C12401"),  # Endocrine System -> Endocrine Gland
    (
        "C12403",
        "C12400",
    ),  # Endocrine System -> Thyroid Gland (transitive, materialized)
}


def _is_ancestor(a: str, b: str) -> bool:
    return (a, b) in _ANCESTORS


def _roles(*pairs: tuple[str, str, str]) -> list[RoleRestriction]:
    return [RoleRestriction(code, filler, label) for code, filler, label in pairs]


@pytest.mark.unit
def test_filter_excluded_drops_negative_axioms() -> None:
    restrictions = _roles(
        ("R101", "C12400", "Disease_Has_Primary_Anatomic_Site"),
        ("R109", "C9999", "Disease_Excludes_Abnormal_Cell"),
    )
    kept = filter_excluded(restrictions)
    assert [r.role_code for r in kept] == ["R101"]


@pytest.mark.unit
def test_most_specific_keeps_only_the_hierarchy_leaf() -> None:
    fillers = {"C12400", "C12401", "C12402", "C12403"}
    # Only Thyroid Gland is a leaf; its ancestors are dropped.
    assert most_specific(fillers, _is_ancestor) == {"C12400"}


@pytest.mark.unit
def test_most_specific_keeps_unrelated_fillers() -> None:
    # Two fillers with no ancestor relationship are both genuine leaves.
    assert most_specific({"C12400", "C777"}, _is_ancestor) == {"C12400", "C777"}


@pytest.mark.unit
def test_most_specific_single_filler_is_unchanged() -> None:
    assert most_specific({"C12400"}, _is_ancestor) == {"C12400"}


@pytest.mark.unit
def test_select_constituents_one_per_single_filler_axis() -> None:
    # C6135's four distinct single-filler axes (design §4.2).
    restrictions = _roles(
        ("R88", "C27970", "Disease_Is_Stage"),
        ("R89", "C90530", "Disease_Has_Stage_System"),
        ("R101", "C12400", "Disease_Has_Primary_Anatomic_Site"),
        ("R105", "C36761", "Disease_Has_Abnormal_Cell"),
    )
    constituents = select_constituents(restrictions, _is_ancestor)
    by_axis = {c.axis: c for c in constituents}
    assert set(by_axis) == {"R88", "R89", "R101", "R105"}
    assert by_axis["R101"].filler_code == "C12400"
    assert all(c.axis_source == "role" for c in constituents)
    # Single-filler axes made no choice, so most_specific stays False.
    assert all(not c.most_specific for c in constituents)


@pytest.mark.unit
def test_select_constituents_collapses_a_multi_filler_axis() -> None:
    # One axis returns Thyroid + its ancestors (ancestor bleed); collapse to the leaf.
    restrictions = _roles(
        ("R101", "C12400", "Disease_Has_Primary_Anatomic_Site"),
        ("R101", "C12401", "Disease_Has_Primary_Anatomic_Site"),
        ("R101", "C12403", "Disease_Has_Primary_Anatomic_Site"),
    )
    constituents = select_constituents(restrictions, _is_ancestor)
    assert len(constituents) == 1
    assert constituents[0].filler_code == "C12400"
    assert constituents[0].most_specific is True  # a choice was made
    assert constituents[0].needs_review is False


@pytest.mark.unit
def test_select_flags_ambiguous_multi_leaf_axis_for_review() -> None:
    # Two unrelated leaves on one axis: keep both, flag for curation (design §6).
    restrictions = _roles(
        ("R101", "C12400", "Disease_Has_Primary_Anatomic_Site"),
        ("R101", "C777", "Disease_Has_Primary_Anatomic_Site"),
    )
    constituents = select_constituents(restrictions, _is_ancestor)
    assert len(constituents) == 2
    assert all(c.needs_review for c in constituents)


@pytest.mark.unit
def test_most_specific_flag_is_per_filler_not_axis_aggregate() -> None:
    # One axis with an ancestor/leaf pair (C12401 -> C12400) AND an unrelated filler
    # (C777). After collapse the leaves are {C12400, C777}: only C12400 was chosen over
    # an ancestor, so only it is most_specific; the axis is still ambiguous (2 leaves).
    restrictions = _roles(
        ("R101", "C12400", "Disease_Has_Primary_Anatomic_Site"),
        ("R101", "C12401", "Disease_Has_Primary_Anatomic_Site"),
        ("R101", "C777", "Disease_Has_Primary_Anatomic_Site"),
    )
    by_filler = {
        c.filler_code: c for c in select_constituents(restrictions, _is_ancestor)
    }
    assert set(by_filler) == {"C12400", "C777"}
    assert by_filler["C12400"].most_specific is True
    assert by_filler["C777"].most_specific is False  # nothing was dropped for it
    assert all(c.needs_review for c in by_filler.values())


@pytest.mark.unit
def test_cyclic_hierarchy_keeps_all_fillers_and_flags_review() -> None:
    # A pathological cycle (A ancestor of B AND B ancestor of A) must not silently drop
    # the whole axis — keep both and flag for curation.
    def cyclic(a: str, b: str) -> bool:
        return {(a, b), (b, a)} & {("CA", "CB"), ("CB", "CA")} != set()

    restrictions = _roles(
        ("R101", "CA", "Disease_Has_Primary_Anatomic_Site"),
        ("R101", "CB", "Disease_Has_Primary_Anatomic_Site"),
    )
    constituents = select_constituents(restrictions, cyclic)
    assert {c.filler_code for c in constituents} == {"CA", "CB"}
    assert all(c.needs_review for c in constituents)


@pytest.mark.unit
def test_select_adds_morphology_from_parent() -> None:
    restrictions = _roles(("R101", "C12400", "Disease_Has_Primary_Anatomic_Site"))
    constituents = select_constituents(
        restrictions, _is_ancestor, parent_morphology="C40384"
    )
    morph = [c for c in constituents if c.axis == MORPHOLOGY_AXIS]
    assert len(morph) == 1
    assert morph[0].filler_code == "C40384"
    assert morph[0].axis_source == "parent"


@pytest.mark.unit
def test_select_excludes_negative_axioms() -> None:
    restrictions = _roles(
        ("R101", "C12400", "Disease_Has_Primary_Anatomic_Site"),
        ("R109", "C9999", "Disease_Excludes_Finding"),
    )
    constituents = select_constituents(restrictions, _is_ancestor)
    assert [c.axis for c in constituents] == ["R101"]


@pytest.mark.unit
def test_select_output_is_deterministic() -> None:
    restrictions = _roles(
        ("R105", "C36761", "Disease_Has_Abnormal_Cell"),
        ("R88", "C27970", "Disease_Is_Stage"),
    )
    first = select_constituents(restrictions, _is_ancestor)
    second = select_constituents(list(reversed(restrictions)), _is_ancestor)
    assert first == second  # stable ordering regardless of input order


# --- A3: route_axis ---


@pytest.mark.unit
def test_route_axis_maps_r101_with_lineage_generic_to_lineage_axis() -> None:
    r = RoleRestriction("R101", "C12704", anchoring_genus="C3010")
    assert route_axis(r) == ASSOCIATED_LINEAGE_AXIS


@pytest.mark.unit
def test_route_axis_keeps_non_r101_on_role_code() -> None:
    r = RoleRestriction("R88", "C27970")
    assert route_axis(r) == "R88"


@pytest.mark.unit
def test_route_axis_keeps_r101_without_genus_on_role_code() -> None:
    r = RoleRestriction("R101", "C12400")
    assert route_axis(r) == "R101"


@pytest.mark.unit
def test_route_axis_keeps_r101_with_non_lineage_genus_on_role_code() -> None:
    r = RoleRestriction("R101", "C12400", anchoring_genus="C4815")
    assert route_axis(r) == "R101"


# --- A3: D20 refinement 1 — genus-sense routing in select_constituents ---


@pytest.mark.unit
def test_lineage_generic_genus_routes_r101_to_lineage_axis() -> None:
    r = [
        RoleRestriction(
            "R101",
            "C12704",
            "Disease_Has_Primary_Anatomic_Site",
            anchoring_genus="C3010",
        ),
        RoleRestriction(
            "R101",
            "C12705",
            "Disease_Has_Primary_Anatomic_Site",
            anchoring_genus="C3010",
        ),
    ]
    cons = select_constituents(r, lambda a, b: False)
    assert {c.filler_code for c in cons} == {"C12704", "C12705"}
    assert all(c.axis == ASSOCIATED_LINEAGE_AXIS for c in cons)


@pytest.mark.unit
def test_site_specific_genus_stays_on_r101() -> None:
    r = [
        RoleRestriction(
            "R101",
            "C12400",
            "Disease_Has_Primary_Anatomic_Site",
            anchoring_genus="C4815",
        )
    ]
    assert select_constituents(r, lambda a, b: False)[0].axis == "R101"


# --- A4: D20 refinement 2 — filler-semantic-type ranking ---


@pytest.mark.unit
def test_semantic_type_ranking_splits_organ_from_region() -> None:
    sem = {
        "C12400": "Body Part, Organ, or Organ Component",
        "C12418": "Anatomical Structure",
        "C13063": "Anatomical Structure",
    }
    r = [
        RoleRestriction("R101", c, "Disease_Has_Primary_Anatomic_Site")
        for c in ("C12400", "C12418", "C13063")
    ]
    cons = {
        c.filler_code: c
        for c in select_constituents(r, lambda a, b: False, semantic_type_of=sem.get)
    }
    assert cons["C12400"].axis == "R101"
    assert cons["C12418"].axis == ASSOCIATED_REGION_AXIS
    assert cons["C13063"].axis == ASSOCIATED_REGION_AXIS


# --- A5: D19 grouping ---


@pytest.mark.unit
def test_coequal_nonnested_leaves_share_a_group_and_are_not_review() -> None:
    sem = {"C12418": "Anatomical Structure", "C13063": "Anatomical Structure"}
    r = [
        RoleRestriction("R101", c, "Disease_Has_Primary_Anatomic_Site")
        for c in ("C12418", "C13063")
    ]
    cons = select_constituents(r, lambda a, b: False, semantic_type_of=sem.get)
    assert {c.axis for c in cons} == {ASSOCIATED_REGION_AXIS}
    assert all(
        c.group == ASSOCIATED_REGION_AXIS and c.needs_review is False for c in cons
    )


@pytest.mark.unit
def test_single_filler_axis_has_no_group() -> None:
    assert (
        select_constituents(
            [RoleRestriction("R88", "C27970", "Disease_Is_Stage")], lambda a, b: False
        )[0].group
        is None
    )


@pytest.mark.unit
def test_lineage_leaves_share_group_and_are_not_review() -> None:
    r = [
        RoleRestriction(
            "R101",
            "C12704",
            "Disease_Has_Primary_Anatomic_Site",
            anchoring_genus="C3010",
        ),
        RoleRestriction(
            "R101",
            "C12705",
            "Disease_Has_Primary_Anatomic_Site",
            anchoring_genus="C3010",
        ),
    ]
    cons = select_constituents(r, lambda a, b: False)
    assert {c.axis for c in cons} == {ASSOCIATED_LINEAGE_AXIS}
    assert all(
        c.group == ASSOCIATED_LINEAGE_AXIS and c.needs_review is False for c in cons
    )


@pytest.mark.unit
def test_semantic_type_ranking_one_organ_one_region() -> None:
    sem = {
        "C12400": "Body Part, Organ, or Organ Component",
        "C12418": "Anatomical Structure",
    }
    r = [
        RoleRestriction("R101", "C12400", "Disease_Has_Primary_Anatomic_Site"),
        RoleRestriction("R101", "C12418", "Disease_Has_Primary_Anatomic_Site"),
    ]
    cons = select_constituents(r, lambda a, b: False, semantic_type_of=sem.get)
    by_filler = {c.filler_code: c for c in cons}
    assert by_filler["C12400"].axis == "R101"
    assert by_filler["C12400"].needs_review is False
    assert by_filler["C12418"].axis == ASSOCIATED_REGION_AXIS
    assert by_filler["C12418"].needs_review is False
    assert by_filler["C12418"].group is None


@pytest.mark.unit
def test_semantic_type_ranking_all_organs_keeps_r101_tie() -> None:
    sem = {
        "C12400": "Body Part, Organ, or Organ Component",
        "C12401": "Body Part, Organ, or Organ Component",
    }
    r = [
        RoleRestriction("R101", c, "Disease_Has_Primary_Anatomic_Site")
        for c in ("C12400", "C12401")
    ]
    cons = select_constituents(r, lambda a, b: False, semantic_type_of=sem.get)
    assert {c.axis for c in cons} == {"R101"}
    assert all(c.needs_review for c in cons)
    assert all(c.group is None for c in cons)


# --- A6: capstone — C6135 golden split ---


@pytest.mark.unit
def test_c6135_r101_family_matches_golden_split() -> None:
    sem = {
        "C12400": "Body Part, Organ, or Organ Component",
        "C12704": "Body Part, Organ, or Organ Component",
        "C12705": "Body System",
        "C12418": "Anatomical Structure",
        "C13063": "Anatomical Structure",
    }
    r = [
        RoleRestriction("R101", "C12400", "Disease_Has_Primary_Anatomic_Site"),
        RoleRestriction(
            "R101",
            "C12704",
            "Disease_Has_Primary_Anatomic_Site",
            anchoring_genus="C3010",
        ),
        RoleRestriction(
            "R101",
            "C12705",
            "Disease_Has_Primary_Anatomic_Site",
            anchoring_genus="C3010",
        ),
        RoleRestriction("R101", "C12418", "Disease_Has_Primary_Anatomic_Site"),
        RoleRestriction("R101", "C13063", "Disease_Has_Primary_Anatomic_Site"),
    ]
    by_axis: dict[str, set[str]] = defaultdict(set)
    for c in select_constituents(r, lambda a, b: False, semantic_type_of=sem.get):
        by_axis[c.axis].add(c.filler_code)
    assert by_axis["R101"] == {"C12400"}
    assert by_axis[ASSOCIATED_LINEAGE_AXIS] == {"C12704", "C12705"}
    assert by_axis[ASSOCIATED_REGION_AXIS] == {"C12418", "C13063"}


# --- Lineage axis exempt from most-specific collapse ---


@pytest.mark.unit
def test_lineage_fillers_preserve_ancestor_relationship() -> None:
    """D20 policy: co-equal lineage senses must be preserved even when in a
    taxonomy ancestor relationship (e.g., Endocrine Gland vs Endocrine System).

    The most_specific collapse must NOT be applied to the lineage axis, otherwise
    the broader sense (Endocrine System, ancestor of Endocrine Gland) would be
    incorrectly dropped.
    """
    # C12705 (Endocrine System) is ancestor of C12704 (Endocrine Gland)

    def _is_anc(a: str, b: str) -> bool:
        return (a, b) in {("C12705", "C12704")}

    r = [
        RoleRestriction(
            "R101",
            "C12704",
            "Disease_Has_Primary_Anatomic_Site",
            anchoring_genus="C3809",  # lineage-generic
        ),
        RoleRestriction(
            "R101",
            "C12705",
            "Disease_Has_Primary_Anatomic_Site",
            anchoring_genus="C3809",
        ),
    ]
    cons = select_constituents(r, _is_anc)
    lineage = [c for c in cons if c.axis == ASSOCIATED_LINEAGE_AXIS]
    # Both must be preserved: most-specific collapse does NOT apply to lineage
    assert len(lineage) == 2
    assert {c.filler_code for c in lineage} == {"C12704", "C12705"}
