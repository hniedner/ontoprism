"""Unit tests for the pre-coordination detector (semantic-type gate + axis count)."""

import pytest

from ontolib.decomposition.detector import detect, label_multi_aspect
from ontolib.decomposition.models import RoleRestriction


def _roles(*pairs: tuple[str, str, str]) -> list[RoleRestriction]:
    return [RoleRestriction(code, filler, label) for code, filler, label in pairs]


# C6135's stated roles (design §4.2 / assessment §5).
_C6135_ROLES = _roles(
    ("R88", "C27970", "Disease_Is_Stage"),
    ("R89", "C90530", "Disease_Has_Stage_System"),
    ("R101", "C12400", "Disease_Has_Primary_Anatomic_Site"),
    ("R105", "C36761", "Disease_Has_Abnormal_Cell"),
)


@pytest.mark.unit
def test_multi_axis_in_scope_concept_is_precoordinated() -> None:
    result = detect("C6135", ["Neoplastic Process"], _C6135_ROLES)
    assert result.is_precoordinated
    assert result.defining_role_count == 4
    assert result.semantic_type == "Neoplastic Process"


@pytest.mark.unit
def test_out_of_scope_semantic_type_fails_the_gate() -> None:
    result = detect("C12400", ["Body Part, Organ, or Organ Component"], _C6135_ROLES)
    assert not result.is_precoordinated


@pytest.mark.unit
def test_gene_concept_fails_the_semantic_type_gate() -> None:
    roles = _roles(("R43", "C17021", "Gene_Plays_Role_In_Process"))
    result = detect("C1234", ["Gene or Genome"], roles)
    assert not result.is_precoordinated


@pytest.mark.unit
def test_any_in_scope_type_passes_the_gate_for_a_multi_typed_concept() -> None:
    # A concept typed BOTH a gene and a neoplasm is in scope; the gate must not depend
    # on which P106 value happens to come back first.
    result = detect("C1", ["Gene or Genome", "Neoplastic Process"], _C6135_ROLES)
    assert result.is_precoordinated
    # The representative type reported is the in-scope one, deterministically.
    assert result.semantic_type == "Neoplastic Process"


@pytest.mark.unit
def test_no_semantic_type_is_out_of_scope() -> None:
    result = detect("C1", [], _C6135_ROLES)
    assert not result.is_precoordinated
    assert result.semantic_type is None


@pytest.mark.unit
def test_single_axis_atomic_concept_is_not_precoordinated() -> None:
    roles = _roles(("R101", "C12400", "Disease_Has_Primary_Anatomic_Site"))
    result = detect("C1", ["Neoplastic Process"], roles)
    assert result.defining_role_count == 1
    assert not result.is_precoordinated


@pytest.mark.unit
def test_single_site_role_plus_parent_morphology_qualifies() -> None:
    # Site role + a morphology-bearing taxonomic parent is genuinely 2-axis (design §14
    # decision 1): it must qualify even though it carries only one role.
    roles = _roles(("R101", "C12400", "Disease_Has_Primary_Anatomic_Site"))
    result = detect("C1", ["Neoplastic Process"], roles, has_parent_morphology=True)
    assert result.is_precoordinated


@pytest.mark.unit
def test_excludes_roles_do_not_count_toward_the_gate() -> None:
    roles = _roles(
        ("R101", "C12400", "Disease_Has_Primary_Anatomic_Site"),
        ("R109", "C9999", "Disease_Excludes_Abnormal_Cell"),
        ("R110", "C8888", "Disease_Excludes_Finding"),
    )
    result = detect("C1", ["Neoplastic Process"], roles)
    # Only the one positive site role counts → single-axis → not pre-coordinated.
    assert result.defining_role_count == 1
    assert not result.is_precoordinated


@pytest.mark.unit
def test_label_signal_bumps_a_single_role_concept_over_the_gate() -> None:
    roles = _roles(("R101", "C12468", "Disease_Has_Primary_Anatomic_Site"))
    result = detect(
        "C35756",
        ["Neoplastic Process"],
        roles,
        label="Lung Carcinoma with Pleural Effusion",
    )
    assert result.label_multi_aspect
    assert result.is_precoordinated  # 1 role + 1 label-signalled axis = 2


@pytest.mark.unit
def test_min_axes_threshold_is_configurable() -> None:
    roles = _roles(("R101", "C12400", "Disease_Has_Primary_Anatomic_Site"))
    result = detect("C1", ["Neoplastic Process"], roles, min_decomposable_axes=1)
    assert result.is_precoordinated


@pytest.mark.unit
@pytest.mark.parametrize(
    "label",
    [
        "Stage IIIB Lung Small Cell Carcinoma with Pleural Effusion AJCC v7",
        "Carcinoma of the Thyroid Gland",
        "Neoplasm (Malignant)",
        "Grade 3 Astrocytoma",
    ],
)
def test_label_multi_aspect_detects_fused_labels(label: str) -> None:
    assert label_multi_aspect(label)


@pytest.mark.unit
@pytest.mark.parametrize("label", ["Thyroid Gland", "Medullary Carcinoma", None, ""])
def test_label_multi_aspect_ignores_atomic_labels(label: str | None) -> None:
    assert not label_multi_aspect(label)
