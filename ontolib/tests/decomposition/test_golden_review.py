from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from scripts.research.golden_review import (
    GoldenSetValidationError,
    load_scorable_golden,
    validate_m1_cohort,
)

if TYPE_CHECKING:
    from pathlib import Path

_SOURCE_IDENTITY = "a" * 64


def _decision(
    status: str,
    *,
    reviewer: str = "Example SME",
    reviewed_at: str = "2026-07-30",
    rationale: str = "Reviewed against the stated NCIt definition.",
) -> dict[str, str]:
    return {
        "status": status,
        "reviewer": reviewer,
        "reviewed_at": reviewed_at,
        "rationale": rationale,
    }


def _artifact(
    concepts: dict[str, object],
    *,
    status: str = "SME-ADJUDICATED",
) -> dict[str, object]:
    return {
        "_meta": {
            "schema_version": 1,
            "status": status,
            "ncit_version": "26.07d",
            "source_identity": _SOURCE_IDENTITY,
        },
        "concepts": concepts,
    }


def _write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.unit
def test_scorer_refuses_an_automated_draft_before_returning_expected_pairs(
    tmp_path: Path,
) -> None:
    path = tmp_path / "draft.json"
    _write(
        path,
        _artifact(
            {
                "C6135": {
                    "label": "Stage III Thyroid Gland Medullary Carcinoma AJCC v7",
                    "constituents": [["op:StageValue", "C27970"]],
                }
            },
            status="AUTO-DRAFT",
        ),
    )

    with pytest.raises(
        GoldenSetValidationError,
        match="not SME-adjudicated",
    ):
        load_scorable_golden(path)


@pytest.mark.unit
def test_scorer_uses_only_accepted_decisions_and_retains_all_decision_states(
    tmp_path: Path,
) -> None:
    path = tmp_path / "adjudicated.json"
    _write(
        path,
        _artifact(
            {
                "C6135": {
                    "label": "Accepted",
                    "constituents": [
                        ["op:StageValue", "C27970"],
                        ["op:PrimarySite", "C12400"],
                    ],
                    "adjudication": _decision("accepted"),
                },
                "C35756": {
                    "label": "Rejected",
                    "constituents": [["op:StageValue", "C27971"]],
                    "adjudication": _decision(
                        "rejected",
                        rationale="The candidate is unsuitable for this oracle.",
                    ),
                },
                "C89995": {
                    "label": "Needs revision",
                    "constituents": [["op:StageValue", "C27972"]],
                    "adjudication": _decision(
                        "revision-needed",
                        rationale="The expected primary-site filler must be revised.",
                    ),
                },
            }
        ),
    )

    golden = load_scorable_golden(path)

    assert golden.ncit_version == "26.07d"
    assert golden.source_identity == _SOURCE_IDENTITY
    assert golden.expected == {
        "C6135": frozenset(
            {
                ("op:StageValue", "C27970"),
                ("op:PrimarySite", "C12400"),
            }
        )
    }
    assert golden.decision_counts == {
        "accepted": 1,
        "rejected": 1,
        "revision-needed": 1,
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("reviewer", "", "reviewer"),
        ("reviewed_at", "", "reviewed_at"),
        ("rationale", "", "rationale"),
        ("status", "pending", "decision status"),
    ],
)
def test_final_artifact_rejects_incomplete_or_pending_adjudication(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    path = tmp_path / "invalid.json"
    adjudication = _decision("accepted")
    adjudication[field] = value
    _write(
        path,
        _artifact(
            {
                "C6135": {
                    "label": "Candidate",
                    "constituents": [],
                    "adjudication": adjudication,
                }
            }
        ),
    )

    with pytest.raises(GoldenSetValidationError, match=message):
        load_scorable_golden(path)


@pytest.mark.unit
def test_m1_cohort_requires_twenty_to_fifty_decisions_and_named_seeds() -> None:
    too_small = {f"C{i}": _decision("accepted") for i in range(19)}

    with pytest.raises(GoldenSetValidationError, match="20 to 50"):
        validate_m1_cohort(too_small)

    missing_seed = {f"C{i}": _decision("accepted") for i in range(20)}
    with pytest.raises(GoldenSetValidationError, match="C35756, C4791, C89995"):
        validate_m1_cohort(missing_seed)

    complete = {f"C{i}": _decision("accepted") for i in range(17)}
    complete.update(
        {
            "C4791": _decision("accepted"),
            "C35756": _decision("rejected"),
            "C89995": _decision("revision-needed"),
        }
    )

    validate_m1_cohort(complete)
