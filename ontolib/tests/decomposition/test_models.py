"""Unit tests for the decomposition value models (pure; no store)."""

import pytest

from ontolib.decomposition.models import (
    Constituent,
    Decomposition,
    DetectionResult,
    RoleRestriction,
)


@pytest.mark.unit
def test_role_restriction_label_is_optional() -> None:
    r = RoleRestriction(role_code="R105", filler_code="C36761")
    assert r.role_label is None
    assert r.role_code == "R105"
    assert r.filler_code == "C36761"


@pytest.mark.unit
def test_constituent_defaults_are_conservative() -> None:
    c = Constituent(axis="R101", filler_code="C12400", axis_source="role")
    # A constituent is not assumed most-specific or reviewed unless stated.
    assert c.most_specific is False
    assert c.needs_review is False


@pytest.mark.unit
def test_models_are_frozen() -> None:
    c = Constituent(axis="R101", filler_code="C12400", axis_source="role")
    with pytest.raises((AttributeError, TypeError)):
        c.filler_code = "C0"  # type: ignore[misc]


@pytest.mark.unit
def test_decomposition_axes_are_the_distinct_constituent_axes() -> None:
    decomp = Decomposition(
        code="C6135",
        semantic_type="Neoplastic Process",
        constituents=[
            Constituent(axis="R88", filler_code="C27970", axis_source="role"),
            Constituent(axis="R101", filler_code="C12400", axis_source="role"),
            Constituent(
                axis="op:Morphology", filler_code="C40384", axis_source="parent"
            ),
            # A second filler on an existing axis must not inflate the axis set.
            Constituent(axis="R101", filler_code="C12468", axis_source="role"),
        ],
    )
    assert decomp.axes == {"R88", "R101", "op:Morphology"}


@pytest.mark.unit
def test_detection_result_carries_the_gate_inputs() -> None:
    d = DetectionResult(
        code="C6135",
        is_precoordinated=True,
        defining_role_count=4,
        semantic_type="Neoplastic Process",
        label_multi_aspect=True,
    )
    assert d.is_precoordinated
    assert d.defining_role_count == 4
