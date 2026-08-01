from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest
from scripts.decomposition_spike import _load_golden_expectations, _metric
from scripts.research.golden_review import GoldenSetValidationError

if TYPE_CHECKING:
    from pathlib import Path


def _write_candidate(path: Path, *, status: str) -> None:
    codes = [f"C{index}" for index in range(16)] + [
        "C4791",
        "C35756",
        "C89995",
        "C6135",
    ]
    payload = {
        "_meta": {
            "schema_version": 2,
            "status": status,
            "ncit_version": "26.07d",
            "source_identity": "a" * 64,
            "sample_manifest_identity": "b" * 64,
            "run_id": "review-run",
            "run_fingerprint_identity": "c" * 64,
            "engine_artifact_identity": "d" * 64,
            "engine_evidence_identity": "f" * 64,
            "corpus_evidence_identity": "5" * 64,
            "detector_identity": "6" * 64,
            "workbook_identity": "e" * 64,
            "reviewer": {
                "name": "Example SME",
                "qualification_or_role": "NCIt ontology curator",
                "reviewed_at": "2026-07-30",
            },
        },
        "concepts": [
            {
                "code": code,
                "label": f"Reviewed {code}",
                "expected": {
                    "outcome": "decomposed",
                    "semantic_types": ["Neoplastic Process"],
                    "constituents": [
                        {
                            "axis": "op:StageValue",
                            "filler": "C27970",
                            "relationship_group": None,
                            "needs_review": False,
                        }
                    ],
                },
                "adjudication": {
                    "status": "accepted",
                    "rationale": "Reviewed against the stated definition.",
                },
            }
            for code in codes
        ],
    }
    payload["artifact_identity"] = hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
    ).hexdigest()
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.unit
def test_decomposition_spike_loads_only_accepted_expectations(tmp_path: Path) -> None:
    path = tmp_path / "golden.json"
    _write_candidate(path, status="SME-ADJUDICATED")

    loaded = _load_golden_expectations(path)

    assert len(loaded) == 20
    assert loaded["C6135"] == frozenset({("op:StageValue", "C27970")})


@pytest.mark.unit
def test_decomposition_spike_refuses_auto_draft_metrics(tmp_path: Path) -> None:
    path = tmp_path / "draft.json"
    _write_candidate(path, status="AUTO-DRAFT")

    with pytest.raises(GoldenSetValidationError, match="not SME-adjudicated"):
        _load_golden_expectations(path)


@pytest.mark.unit
def test_decomposition_spike_direct_entry_point_resolves_imports() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/decomposition_spike.py"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert "ModuleNotFoundError" not in result.stderr
    assert "not SME-adjudicated" in result.stderr


@pytest.mark.unit
def test_decomposition_spike_metrics_are_undefined_without_denominators() -> None:
    assert _metric(1.0, 0) == "undefined"
    assert _metric(0.5, 2) == "0.50"
