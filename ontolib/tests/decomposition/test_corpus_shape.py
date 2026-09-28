"""Line-oriented corpus-shape comparisons stay deterministic."""

from collections import Counter

import pytest
from scripts.corpus_shape import render_report

pytestmark = pytest.mark.unit


def test_against_renders_selected_minus_baseline_for_every_line() -> None:
    selected = Counter({"outcomes.decomposed": 3, "constituents.total": 5})
    baseline = Counter({"outcomes.decomposed": 1, "review_flags.constituent.total": 2})

    report = render_report("selected", selected, against=("baseline", baseline))

    assert report.splitlines() == [
        "run_id=selected",
        "constituents.total=5",
        "outcomes.decomposed=3",
        "against_run_id=baseline",
        "difference.selected-minus-against.constituents.total=+5",
        "difference.selected-minus-against.outcomes.decomposed=+2",
        "difference.selected-minus-against.review_flags.constituent.total=-2",
    ]
