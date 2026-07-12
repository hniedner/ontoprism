from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from ontolib.repositories.xref.mapping_score import (
    load_golden_mappings,
    score_mappings,
)
from ontolib.repositories.xref.vocab import EXACT_MATCH, NARROW_MATCH

if TYPE_CHECKING:
    from pathlib import Path


def _gold(
    exact: list[tuple[str, str]], narrow: list[tuple[str, str]] | None = None
) -> list[dict]:
    out = [
        {"subject_id": s, "predicate_id": EXACT_MATCH, "object_id": o} for s, o in exact
    ]
    if narrow:
        out += [
            {"subject_id": s, "predicate_id": NARROW_MATCH, "object_id": o}
            for s, o in narrow
        ]
    return out


def _act(exact: list[tuple[str, str]]) -> list[dict]:
    return [
        {"subject_id": s, "predicate_id": EXACT_MATCH, "object_id": o} for s, o in exact
    ]


@pytest.mark.unit
def test_perfect_match() -> None:
    golden = _gold([("C12400", "UBERON:0002046")])
    actual = _act([("C12400", "UBERON:0002046")])
    result = score_mappings(golden, actual, predicate=EXACT_MATCH)
    assert result.precision == 1.0
    assert result.recall == 1.0
    assert result.f1 == 1.0
    assert result.true_positive == 1
    assert result.missing == []
    assert result.extra == []


@pytest.mark.unit
def test_wrong_object_drops_precision() -> None:
    golden = _gold([("C12400", "UBERON:0002046")])
    actual = _act([("C12400", "UBERON:0009999")])
    result = score_mappings(golden, actual, predicate=EXACT_MATCH)
    assert result.precision == 0.0
    assert result.recall == 0.0
    assert result.f1 == 0.0
    assert result.true_positive == 0
    assert result.missing == [("C12400", "UBERON:0002046")]
    assert result.extra == [("C12400", "UBERON:0009999")]


@pytest.mark.unit
def test_narrow_match_excluded_from_exact_scoring() -> None:
    golden = _gold(
        exact=[("C12400", "UBERON:0002046")],
        narrow=[("C19184", "UBERON:0001155")],
    )
    actual = _act([("C12400", "UBERON:0002046")])
    result = score_mappings(golden, actual, predicate=EXACT_MATCH)
    assert result.precision == 1.0
    assert result.recall == 1.0
    assert result.f1 == 1.0
    assert result.true_positive == 1
    assert result.missing == []
    assert result.extra == []


@pytest.mark.unit
def test_empty_actual_returns_zero() -> None:
    golden = _gold([("C12400", "UBERON:0002046")])
    result = score_mappings(golden, [], predicate=EXACT_MATCH)
    assert result.precision == 0.0
    assert result.recall == 0.0
    assert result.f1 == 0.0


@pytest.mark.unit
def test_empty_golden_empty_actual_returns_zero() -> None:
    result = score_mappings([], [], predicate=EXACT_MATCH)
    assert result.precision == 0.0
    assert result.recall == 0.0
    assert result.f1 == 0.0
    assert result.true_positive == 0


@pytest.mark.unit
def test_load_golden_mappings(tmp_path: Path) -> None:
    fixture = {
        "_meta": {},
        "mappings": [
            {
                "subject_id": "C12400",
                "predicate_id": EXACT_MATCH,
                "object_id": "UBERON:0002046",
            },
        ],
    }
    p = tmp_path / "golden.json"
    p.write_text(json.dumps(fixture))
    loaded = load_golden_mappings(str(p))
    assert len(loaded) == 1
    assert loaded[0]["subject_id"] == "C12400"
    assert loaded[0]["object_id"] == "UBERON:0002046"


@pytest.mark.unit
def test_scoring_partial_match() -> None:
    golden = _gold(
        [
            ("C12400", "UBERON:0002046"),
            ("C12468", "UBERON:0002048"),
            ("C12971", "UBERON:0000310"),
        ]
    )
    actual = _act([("C12400", "UBERON:0002046"), ("C12468", "UBERON:0002048")])
    result = score_mappings(golden, actual, predicate=EXACT_MATCH)
    assert result.precision == 1.0
    assert result.recall == 2 / 3
    assert abs(result.f1 - 0.8) < 1e-9
    assert result.true_positive == 2
    assert result.missing == [("C12971", "UBERON:0000310")]
    assert result.extra == []
