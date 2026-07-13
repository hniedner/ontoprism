from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class MappingScore:
    precision: float
    recall: float
    f1: float
    true_positive: int
    missing: list[tuple[str, str]] = field(default_factory=list)
    extra: list[tuple[str, str]] = field(default_factory=list)


def _f1(precision: float, recall: float) -> float:
    total = precision + recall
    return (2 * precision * recall / total) if total else 0.0


def _predicate_pairs(
    records: list[dict[str, str]], predicate: str
) -> set[tuple[str, str]]:
    return {
        (m["subject_id"], m["object_id"])
        for m in records
        if m["predicate_id"] == predicate
    }


def score_mappings(
    golden: list[dict[str, str]],
    actual: list[dict[str, str]],
    *,
    predicate: str,
) -> MappingScore:
    gold = _predicate_pairs(golden, predicate)
    act = _predicate_pairs(actual, predicate)
    tp = gold & act
    precision = len(tp) / len(act) if act else 0.0
    recall = len(tp) / len(gold) if gold else 0.0
    return MappingScore(
        precision=precision,
        recall=recall,
        f1=_f1(precision, recall),
        true_positive=len(tp),
        missing=sorted(gold - act),
        extra=sorted(act - gold),
    )


def load_golden_mappings(path: str | Path) -> list[dict[str, str]]:
    with open(path) as f:
        data = json.load(f)
    return data["mappings"]
