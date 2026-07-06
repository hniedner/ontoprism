"""Unit tests for the decomposition extraction scorer."""

import pytest

from ontolib.decomposition.score import score


@pytest.mark.unit
def test_exact_match_scores_perfectly() -> None:
    golden = {("R88", "C27970"), ("R101", "C12400")}
    s = score(golden, set(golden))
    assert s.exact
    assert s.precision == 1.0
    assert s.recall == 1.0
    assert s.f1 == 1.0
    assert not s.missing
    assert not s.extra


@pytest.mark.unit
def test_reports_missing_and_extra() -> None:
    golden = {("R88", "C27970"), ("R101", "C12400"), ("R105", "C36761")}
    actual = {("R88", "C27970"), ("R105", "C36825"), ("R108", "C41457")}
    s = score(golden, actual)
    assert s.true_positive == 1  # only R88/C27970 matches
    assert s.missing == frozenset({("R101", "C12400"), ("R105", "C36761")})
    assert s.extra == frozenset({("R105", "C36825"), ("R108", "C41457")})
    assert not s.exact
    assert s.precision == pytest.approx(1 / 3)
    assert s.recall == pytest.approx(1 / 3)


@pytest.mark.unit
def test_over_collection_tanks_precision_but_keeps_recall() -> None:
    # The §6.2 failure mode: everything intended is found, plus a lot of noise.
    golden = {("R88", "C27970"), ("R101", "C12400")}
    actual = golden | {("R108", f"C{i}") for i in range(10)}
    s = score(golden, actual)
    assert s.recall == 1.0  # nothing intended was missed
    assert s.precision == pytest.approx(2 / 12)  # but precision collapses
    assert s.missing == frozenset()


@pytest.mark.unit
def test_empty_extraction_is_defined() -> None:
    s = score({("R88", "C27970")}, set())
    assert s.recall == 0.0
    assert s.precision == 1.0  # vacuously (nothing wrong was emitted)
    assert s.missing == frozenset({("R88", "C27970")})


@pytest.mark.unit
def test_empty_golden_is_defined() -> None:
    s = score(set(), {("R88", "C27970")})
    assert s.recall == 1.0
    assert s.precision == 0.0
