"""Unit tests for the axis catalogue: scope gate + Excludes/defining classification."""

import pytest

from ontolib.decomposition.axes import (
    ASSOCIATED_LINEAGE_AXIS,
    ASSOCIATED_REGION_AXIS,
    IN_SCOPE_SEMANTIC_TYPES,
    LINEAGE_GENERIC_GENERA,
    MORPHOLOGY_AXIS,
    ORGAN_SEMANTIC_TYPE,
    PRIMARY_SITE_ROLE,
    is_defining_role,
    is_excluded_role,
    is_in_scope,
    is_lineage_generic,
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


@pytest.mark.unit
@pytest.mark.parametrize(
    ("genus_code", "expected"),
    [
        ("C3010", True),
        ("C3809", True),
        ("C3773", True),
        ("C12400", False),
        (None, False),
    ],
)
def test_is_lineage_generic(genus_code: str | None, expected: bool) -> None:
    assert is_lineage_generic(genus_code) is expected


@pytest.mark.unit
def test_associated_lineage_axis_is_op_axis() -> None:
    assert ASSOCIATED_LINEAGE_AXIS.startswith("op:")


@pytest.mark.unit
def test_associated_region_axis_is_op_axis() -> None:
    assert ASSOCIATED_REGION_AXIS.startswith("op:")


@pytest.mark.unit
def test_primary_site_role_is_r101() -> None:
    assert PRIMARY_SITE_ROLE == "R101"


@pytest.mark.unit
def test_lineage_generic_genera_is_frozenset_of_codes() -> None:
    assert isinstance(LINEAGE_GENERIC_GENERA, frozenset)
    assert "C3010" in LINEAGE_GENERIC_GENERA


@pytest.mark.unit
def test_organ_semantic_type_constant() -> None:
    assert ORGAN_SEMANTIC_TYPE == "Body Part, Organ, or Organ Component"


@pytest.mark.unit
def test_lineage_generic_genera_scope_is_endocrine_only() -> None:
    """LINEAGE_GENERIC_GENERA is validated for endocrine/neuroendocrine lineage.

    D20 specifies the whitelist {C3010, C3809, C3773} was confirmed via C6135.
    Other lineage families (hematopoietic, germ cell) may need additional genera
    but are not yet validated. This test documents the current scope.

    Expansion requires:
    1. Empirical evidence from NCIt showing R101 restrictions on candidate genera
    2. Manual review confirming the genus represents lineage/histology classification
    3. Addition to D20 decision record
    """
    # All current members are endocrine/neuroendocrine
    expected = frozenset({"C3010", "C3809", "C3773"})
    assert expected == LINEAGE_GENERIC_GENERA
    # C3010 = Endocrine Neoplasm, C3809 = Neuroendocrine Neoplasm,
    # C3773 = Neuroendocrine Carcinoma

    # Hematopoietic genera (C3209 Hematopoietic Neoplasm) are NOT in list
    assert "C3209" not in LINEAGE_GENERIC_GENERA
    # Germ cell genera (C4144 Germ Cell Neoplasm) are NOT in list
    assert "C4144" not in LINEAGE_GENERIC_GENERA
