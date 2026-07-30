"""Behavioral contracts for the univocal decomposition relation catalogue."""

import pytest

from ontolib.decomposition.axis_contracts import (
    AXIS_CONTRACTS,
    AxisContract,
    normalized_axis_for_role,
)


@pytest.mark.unit
def test_required_univocal_axes_have_complete_semantic_contracts() -> None:
    required = {
        "op:PrimarySite",
        "op:MetastaticSite",
        "op:AssociatedSite",
        "op:AssociatedRegion",
        "op:CellOrigin",
        "op:CellType",
        "op:MolecularAbnormality",
        "op:StageValue",
        "op:StageSystem",
        "op:Laterality",
        "op:WithFinding",
    }

    assert required <= set(AXIS_CONTRACTS)
    assert all(
        isinstance(contract, AxisContract) for contract in AXIS_CONTRACTS.values()
    )
    for axis, contract in AXIS_CONTRACTS.items():
        assert contract.axis == axis
        assert contract.label
        assert contract.definition
        assert contract.domain_code
        assert contract.domain_label
        assert contract.range_code
        assert contract.range_label
        assert contract.provenance


@pytest.mark.unit
def test_defining_source_roles_map_to_distinct_normalized_axes() -> None:
    expected = {
        "R88": "op:StageValue",
        "R100": "op:AssociatedSite",
        "R101": "op:PrimarySite",
        "R102": "op:MetastaticSite",
        "R103": "op:NormalTissueOrigin",
        "R104": "op:CellOrigin",
        "R105": "op:CellType",
        "R106": "op:MolecularAbnormality",
        "R107": "op:CytogeneticAbnormality",
        "R108": "op:ClinicalFinding",
        "R110": "op:Grade",
    }

    assert {role: normalized_axis_for_role(role) for role in expected} == expected
    assert normalized_axis_for_role("R999") is None


@pytest.mark.unit
def test_source_role_provenance_is_invertible_for_direct_mappings() -> None:
    for role in ("R100", "R101", "R102", "R104", "R105", "R106", "R108"):
        axis = normalized_axis_for_role(role)
        assert axis is not None
        assert AXIS_CONTRACTS[axis].source_roles == (role,)
