from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from scripts.decomposition_spike import _load_golden_expectations
from scripts.research.golden_review import GoldenSetValidationError

if TYPE_CHECKING:
    from pathlib import Path


def _write_candidate(path: Path, *, status: str) -> None:
    path.write_text(
        json.dumps(
            {
                "_meta": {
                    "schema_version": 1,
                    "status": status,
                    "ncit_version": "26.07d",
                    "source_identity": "a" * 64,
                },
                "concepts": {
                    "C6135": {
                        "label": "Reviewed concept",
                        "constituents": [["op:StageValue", "C27970"]],
                        "adjudication": {
                            "status": "accepted",
                            "reviewer": "Example SME",
                            "reviewed_at": "2026-07-30",
                            "rationale": "Reviewed against the stated definition.",
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.unit
def test_decomposition_spike_loads_only_accepted_expectations(tmp_path: Path) -> None:
    path = tmp_path / "golden.json"
    _write_candidate(path, status="SME-ADJUDICATED")

    assert _load_golden_expectations(path) == {
        "C6135": frozenset({("op:StageValue", "C27970")})
    }


@pytest.mark.unit
def test_decomposition_spike_refuses_auto_draft_metrics(tmp_path: Path) -> None:
    path = tmp_path / "draft.json"
    _write_candidate(path, status="AUTO-DRAFT")

    with pytest.raises(GoldenSetValidationError, match="not SME-adjudicated"):
        _load_golden_expectations(path)
