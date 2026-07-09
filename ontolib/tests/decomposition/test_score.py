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


@pytest.mark.unit
def test_needs_review_pairs_are_neither_credited_nor_penalized() -> None:
    # Issue #44's DoD scores with needs_review excluded. A flagged extra must not
    # tank precision, and a flagged golden pair must not tank recall.
    golden = {("R88", "C27970"), ("R101", "C12400")}
    actual = {("R88", "C27970"), ("R101", "C12400"), ("R101", "C12418")}
    s = score(golden, actual, needs_review={("R101", "C12418")})
    assert s.precision == 1.0
    assert s.recall == 1.0
    assert s.exact
    assert s.deferred == frozenset({("R101", "C12418")})


@pytest.mark.unit
def test_flagged_golden_pair_is_not_a_recall_miss() -> None:
    golden = {("R88", "C27970"), ("R101", "C12418")}
    s = score(golden, {("R88", "C27970")}, needs_review={("R101", "C12418")})
    assert s.recall == 1.0
    assert not s.missing
    assert s.deferred == frozenset({("R101", "C12418")})


@pytest.mark.unit
def test_unflagged_wrong_filler_still_penalized_when_review_set_given() -> None:
    # The exclusion must be surgical: only the flagged pair is spared.
    golden = {("R101", "C12400")}
    actual = {("R101", "C12418"), ("R105", "C36825")}
    s = score(golden, actual, needs_review={("R101", "C12418")})
    assert s.extra == frozenset({("R105", "C36825")})
    assert s.missing == frozenset({("R101", "C12400")})
    assert s.precision == 0.0
    assert s.recall == 0.0


@pytest.mark.unit
def test_multi_valued_axis_scores_each_filler_independently() -> None:
    # D19: co-equal non-nested fillers are preserved as group members, so one axis
    # may carry several fillers and each is scored on its own.
    golden = {("op:AssociatedRegion", "C12418"), ("op:AssociatedRegion", "C13063")}
    s = score(golden, {("op:AssociatedRegion", "C12418")})
    assert s.recall == pytest.approx(0.5)
    assert s.precision == 1.0
    assert s.missing == frozenset({("op:AssociatedRegion", "C13063")})


@pytest.mark.unit
def test_deferred_defaults_empty_and_scoring_is_unchanged_without_review_set() -> None:
    golden = {("R88", "C27970")}
    s = score(golden, {("R88", "C27970"), ("R108", "C1")})
    assert s.deferred == frozenset()
    assert s.precision == pytest.approx(0.5)
