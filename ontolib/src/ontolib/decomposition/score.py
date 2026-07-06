"""Precision/recall scoring for decomposition extraction against a curated golden set.

The extraction of stated pre-coordination is a curation-heavy problem (engine design
§6.2): a genus-chain walk over-collects, and most-specific selection can pick the wrong
filler. So the research loop is: curate ``{concept -> intended (axis, filler) pairs}``,
run a candidate extractor, and score precision/recall against the golden set — iterating
the boundary heuristic until it converges. This module is the pure, tested scorer.
"""

from __future__ import annotations

from dataclasses import dataclass

# An (axis, filler) constituent pair, e.g. ("R101", "C12400").
Constituent = tuple[str, str]


@dataclass(frozen=True, slots=True)
class ExtractionScore:
    """How a candidate extraction compares to the intended (golden) constituents."""

    expected: int
    actual: int
    true_positive: int
    missing: frozenset[Constituent]  # in golden, not extracted (recall misses)
    extra: frozenset[Constituent]  # extracted, not in golden (precision misses)

    @property
    def precision(self) -> float:
        """Fraction of extracted constituents correct (1.0 when none extracted)."""
        return self.true_positive / self.actual if self.actual else 1.0

    @property
    def recall(self) -> float:
        """Fraction of intended constituents extracted (1.0 when none intended)."""
        return self.true_positive / self.expected if self.expected else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def exact(self) -> bool:
        """True when the extraction matches the golden set exactly."""
        return not self.missing and not self.extra


def score(expected: set[Constituent], actual: set[Constituent]) -> ExtractionScore:
    """Score an *actual* extraction against the *expected* golden constituents."""
    true_positive = expected & actual
    return ExtractionScore(
        expected=len(expected),
        actual=len(actual),
        true_positive=len(true_positive),
        missing=frozenset(expected - actual),
        extra=frozenset(actual - expected),
    )
