"""Unit tests for the axis catalogue: scope gate + Excludes/defining classification."""

import pytest

from ontolib.decomposition.axes import (
    IN_SCOPE_SEMANTIC_TYPES,
    MORPHOLOGY_AXIS,
    is_defining_role,
    is_excluded_role,
    is_in_scope,
)
from ontolib.decomposition.models import RoleRestriction


@pytest.mark.unit
@pytest.mark.parametrize(
    "semantic_type",
    ["Neoplastic Process", "Disease or Syndrome", "Cell or Molecular Dysfunction"],
)
def test_disease_neoplasm_types_are_in_scope(semantic_type: str) -> None:
    assert is_in_scope(semantic_type)
    assert semantic_type in IN_SCOPE_SEMANTIC_TYPES


@pytest.mark.unit
@pytest.mark.parametrize(
    "semantic_type",
    ["Gene or Genome", "Amino Acid, Peptide, or Protein", "Enzyme", None],
)
def test_molecular_and_unknown_types_are_out_of_scope(
    semantic_type: str | None,
) -> None:
    # The gene/protein role families are excluded by design (design §2); an unknown
    # (null) semantic type is not in scope either.
    assert not is_in_scope(semantic_type)


@pytest.mark.unit
@pytest.mark.parametrize(
    "label",
    ["Disease_Excludes_Abnormal_Cell", "Disease_Excludes_Finding"],
)
def test_excludes_roles_are_recognized(label: str) -> None:
    assert is_excluded_role(label)


@pytest.mark.unit
@pytest.mark.parametrize(
    "label",
    [
        "Disease_Has_Abnormal_Cell",
        "Disease_Has_Primary_Anatomic_Site",
        "Disease_Is_Stage",
        None,  # a label-less restriction is not an Excludes negative axiom
    ],
)
def test_non_excludes_roles_are_not_flagged(label: str | None) -> None:
    assert not is_excluded_role(label)


@pytest.mark.unit
def test_defining_role_excludes_negative_axioms() -> None:
    positive = RoleRestriction("R105", "C36761", "Disease_Has_Abnormal_Cell")
    negative = RoleRestriction("R109", "C12345", "Disease_Excludes_Abnormal_Cell")
    assert is_defining_role(positive)
    assert not is_defining_role(negative)


@pytest.mark.unit
def test_morphology_axis_is_an_ontoprism_axis() -> None:
    # Morphology is carried by the taxonomic parent, not a role, so it needs its own
    # op: axis identifier (design §6).
    assert MORPHOLOGY_AXIS.startswith("op:")
