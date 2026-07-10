"""Unit tests for roundtrip_fidelity metric (D21.3)."""

import pytest

from ontolib.decomposition.fidelity import roundtrip_fidelity
from ontolib.decomposition.models import Constituent, Decomposition, RoleRestriction
from ontolib.decomposition.run import _compute_fidelity


@pytest.mark.unit
def test_fidelity_is_one_when_emitted_covers_stated() -> None:
    stated = {("R88", "C27970"), ("R101", "C12400")}
    emitted = {("R88", "C27970"), ("R101", "C12400"), ("op:AssociatedRegion", "C13063")}
    assert roundtrip_fidelity(emitted, stated) == 1.0


@pytest.mark.unit
def test_fidelity_is_fractional_when_a_stated_restriction_is_missing() -> None:
    stated = {("R88", "C27970"), ("R101", "C12400")}
    emitted = {("R88", "C27970")}
    assert roundtrip_fidelity(emitted, stated) == 0.5


@pytest.mark.unit
def test_fidelity_is_zero_when_nothing_matches() -> None:
    stated = {("R88", "C27970"), ("R101", "C12400")}
    emitted = {("R105", "C36761")}
    assert roundtrip_fidelity(emitted, stated) == 0.0


@pytest.mark.unit
def test_fidelity_is_one_when_stated_is_empty() -> None:
    stated: set[tuple[str, str]] = set()
    emitted = {("R88", "C27970")}
    assert roundtrip_fidelity(emitted, stated) == 1.0


@pytest.mark.unit
def test_fidelity_uses_axis_not_role_code() -> None:
    """D19/D20 routed axes (op:AssociatedRegion) must match by axis name."""
    stated = {("op:AssociatedRegion", "C13063")}
    emitted = {("op:AssociatedRegion", "C13063")}
    assert roundtrip_fidelity(emitted, stated) == 1.0


@pytest.mark.unit
def test_compute_fidelity_returns_none_for_empty_stated_roles() -> None:
    dec = Decomposition(code="C100", semantic_type=None, constituents=[])
    assert _compute_fidelity(dec, []) is None


@pytest.mark.unit
def test_compute_fidelity_computes_role_coverage() -> None:
    dec = Decomposition(
        code="C6135",
        semantic_type="Neoplastic Process",
        constituents=[
            Constituent(axis="R88", filler_code="C27970", axis_source="role"),
            Constituent(axis="R101", filler_code="C12400", axis_source="role"),
            Constituent(
                axis="op:Morphology", filler_code="C3879", axis_source="parent"
            ),
        ],
    )

    stated_roles = [
        RoleRestriction(
            role_code="R88",
            filler_code="C27970",
            role_label="Disease_Has_Associated_Site",
        ),
        RoleRestriction(
            role_code="R101",
            filler_code="C12400",
            role_label="Disease_Has_Normal_Cell_Origin",
        ),
    ]

    assert _compute_fidelity(dec, stated_roles) == 1.0


@pytest.mark.unit
def test_compute_fidelity_excludes_excluded_roles() -> None:
    dec = Decomposition(
        code="C100",
        semantic_type="Disease",
        constituents=[
            Constituent(axis="R88", filler_code="C27970", axis_source="role"),
        ],
    )

    stated_roles = [
        RoleRestriction(
            role_code="R88",
            filler_code="C27970",
            role_label="Disease_Has_Associated_Site",
        ),
        RoleRestriction(
            role_code="R139",
            filler_code="C123",
            role_label="Disease_Excludes_Abnormal_Cell",
        ),
    ]

    assert _compute_fidelity(dec, stated_roles) == 1.0
