"""Fail-closed loading contracts for the SME-adjudicated decomposition oracle."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping

DecisionStatus = Literal["accepted", "rejected", "revision-needed"]
ConstituentPair = tuple[str, str]

_ADJUDICATED_STATUS = "SME-ADJUDICATED"
_DECISION_STATUSES: tuple[DecisionStatus, ...] = (
    "accepted",
    "rejected",
    "revision-needed",
)
_M1_REQUIRED_SEEDS = frozenset({"C4791", "C35756", "C89995"})
_SOURCE_IDENTITY = re.compile(r"^[0-9a-f]{64}$")
_PAIR_LENGTH = 2
_M1_MIN_CONCEPTS = 20
_M1_MAX_CONCEPTS = 50


class GoldenSetValidationError(ValueError):
    """The candidate artifact cannot be trusted as an SME oracle."""


@dataclass(frozen=True, slots=True)
class ScorableGoldenSet:
    """Validated accepted expectations plus the complete adjudication census."""

    ncit_version: str
    source_identity: str
    labels: dict[str, str]
    expected: dict[str, frozenset[ConstituentPair]]
    decisions: dict[str, DecisionStatus]
    decision_counts: dict[DecisionStatus, int]


def _object(value: object, *, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise GoldenSetValidationError(f"{field} must be a JSON object")
    return value


def _nonempty_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GoldenSetValidationError(f"{field} must be non-empty")
    return value.strip()


def _decision(raw: object, *, code: str) -> DecisionStatus:
    decision = _object(raw, field=f"{code} adjudication")
    status = decision.get("status")
    if status not in _DECISION_STATUSES:
        raise GoldenSetValidationError(
            f"{code} decision status must be accepted, rejected, or revision-needed"
        )
    _nonempty_text(decision.get("reviewer"), field=f"{code} reviewer")
    reviewed_at = _nonempty_text(
        decision.get("reviewed_at"),
        field=f"{code} reviewed_at",
    )
    try:
        date.fromisoformat(reviewed_at)
    except ValueError as error:
        raise GoldenSetValidationError(
            f"{code} reviewed_at must be an ISO date"
        ) from error
    _nonempty_text(decision.get("rationale"), field=f"{code} rationale")
    return status


def _constituents(raw: object, *, code: str) -> frozenset[ConstituentPair]:
    if not isinstance(raw, list):
        raise GoldenSetValidationError(f"{code} constituents must be a JSON array")
    pairs: list[ConstituentPair] = []
    for index, item in enumerate(raw):
        if (
            not isinstance(item, list)
            or len(item) != _PAIR_LENGTH
            or not all(isinstance(value, str) and value for value in item)
        ):
            raise GoldenSetValidationError(
                f"{code} constituents[{index}] must be a non-empty axis/filler pair"
            )
        pairs.append((item[0], item[1]))
    if len(pairs) != len(set(pairs)):
        raise GoldenSetValidationError(f"{code} constituents must be unique")
    return frozenset(pairs)


def load_scorable_golden(path: str | Path) -> ScorableGoldenSet:
    """Load accepted expectations only after proving human adjudication provenance."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GoldenSetValidationError(f"cannot read golden set: {error}") from error
    root = _object(raw, field="golden set")
    meta = _object(root.get("_meta"), field="_meta")
    if meta.get("schema_version") != 1:
        raise GoldenSetValidationError("_meta.schema_version must equal 1")
    if meta.get("status") != _ADJUDICATED_STATUS:
        raise GoldenSetValidationError(
            "golden set is not SME-adjudicated; automated drafts cannot be scored"
        )
    ncit_version = _nonempty_text(
        meta.get("ncit_version"),
        field="_meta.ncit_version",
    )
    source_identity = _nonempty_text(
        meta.get("source_identity"),
        field="_meta.source_identity",
    )
    if _SOURCE_IDENTITY.fullmatch(source_identity) is None:
        raise GoldenSetValidationError(
            "_meta.source_identity must be a lowercase SHA-256 digest"
        )

    concepts = _object(root.get("concepts"), field="concepts")
    if not concepts:
        raise GoldenSetValidationError("concepts must not be empty")
    expected: dict[str, frozenset[ConstituentPair]] = {}
    labels: dict[str, str] = {}
    decisions: dict[str, DecisionStatus] = {}
    for code, raw_entry in concepts.items():
        entry = _object(raw_entry, field=f"{code} entry")
        labels[code] = _nonempty_text(entry.get("label"), field=f"{code} label")
        status = _decision(entry.get("adjudication"), code=code)
        decisions[code] = status
        pairs = _constituents(entry.get("constituents"), code=code)
        if status == "accepted":
            expected[code] = pairs

    counts = Counter(decisions.values())
    return ScorableGoldenSet(
        ncit_version=ncit_version,
        source_identity=source_identity,
        labels=labels,
        expected=expected,
        decisions=decisions,
        decision_counts={status: counts[status] for status in _DECISION_STATUSES},
    )


def validate_m1_cohort(
    decisions: Mapping[str, Mapping[str, str]],
) -> None:
    """Enforce #57's cohort size and mandatory named seed decisions."""
    if not _M1_MIN_CONCEPTS <= len(decisions) <= _M1_MAX_CONCEPTS:
        raise GoldenSetValidationError(
            "M1 golden set must contain 20 to 50 adjudicated concepts"
        )
    missing = sorted(_M1_REQUIRED_SEEDS - decisions.keys())
    if missing:
        raise GoldenSetValidationError(
            "M1 golden set is missing named seeds: " + ", ".join(missing)
        )
