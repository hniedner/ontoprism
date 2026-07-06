"""Unit tests for filler selection: Excludes filter, most-specific, morphology."""

import pytest

from ontolib.decomposition.axes import MORPHOLOGY_AXIS
from ontolib.decomposition.filler_selection import (
    filter_excluded,
    most_specific,
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
