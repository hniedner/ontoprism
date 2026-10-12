"""Closed evidence representation rejects false approval and invalid locators."""

import json

import pytest
from pydantic import ValidationError

from ontolib.decomposition.evidence_models import (
    Applicability,
    LedgerAssessment,
    LedgerEntry,
    PolicyChoice,
)
from ontolib.decomposition.source_support import StatedFillerLocator

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "kind", ["axis-assignment", "collapse", "grouping", "nlp-derivation"]
)
def test_policy_kinds_accept_explicit_open_choice(kind: str) -> None:
    choice = PolicyChoice(
        kind=kind, policy="routing-v1", rationale="unresolved", state="open"
    )
    assert choice.state == "open"


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "genus", "role_code": "R108"},
        {"role_code": None},
        {"role_code": "invalid"},
        {"filler_code": "invalid"},
        {"anchor_code": "invalid"},
        {"structural_path": [-1]},
    ],
)
def test_shared_locator_rejects_invalid_source_coordinates(change: dict) -> None:
    locator = {
        "kind": "restriction",
        "anchor_code": "C1",
        "role_code": "R108",
        "filler_code": "C2",
        "depth": 0,
        "structural_path": [0],
    }
    with pytest.raises(ValidationError):
        StatedFillerLocator(**(locator | change))


def payload() -> dict:
    return {
        "concept_code": "C1",
        "axis": "op:Morphology",
        "filler_code": "C2",
        "source_release": "26.07d",
        "scope_root": "C3262",
        "status": "provisional",
        "support": {"axis_source": "nlp"},
    }


@pytest.mark.parametrize(
    "change",
    [
        {"status": "needs-SME"},
        {"status": "evidence-supported"},
        {"needs_sme_reasons": [{"kind": "ambiguous", "rationale": "review"}]},
        {"support": {"axis_source": "p334"}},
        {
            "support": {
                "axis_source": "nlp",
                "contexts": [
                    {
                        "kind": "genus",
                        "anchor_code": "C1",
                        "role_code": None,
                        "filler_code": "C2",
                        "depth": 0,
                        "structural_path": [],
                    }
                ],
            }
        },
        {
            "evidence": [
                {
                    "tier": 0,
                    "kind": "curated-source",
                    "source": "NCIt",
                    "source_release": "26.07d",
                    "locator": "R108",
                    "statement": "stated",
                    "context_indexes": [0],
                }
            ]
        },
    ],
)
def test_assessment_rejects_inconsistent_status_support_or_evidence(
    change: dict,
) -> None:
    with pytest.raises(ValidationError):
        LedgerAssessment.model_validate_json(json.dumps(payload() | change))


def test_supported_p334_preserves_annotation_path_and_rejects_wrong_endpoint() -> None:
    data = payload() | {
        "axis": "op:HistologyAnchor",
        "support": {
            "axis_source": "p334",
            "p334": {
                "eligible_values": ["8000/3"],
                "other_values": [],
                "path": ["C1", "C2"],
            },
        },
    }
    result = LedgerAssessment.model_validate_json(json.dumps(data))
    assert result.support.p334.path == ("C1", "C2")
    data["filler_code"] = "C3"
    with pytest.raises(ValidationError, match="endpoints"):
        LedgerAssessment.model_validate_json(json.dumps(data))


@pytest.mark.parametrize(
    "data",
    [
        {"state": "needs-review", "decision_index": 0},
        {"state": "applicable", "decision_index": None},
        {"state": "no-decision", "decision_index": 0, "reason": "none"},
    ],
)
def test_applicability_requires_reference_and_reason(data: dict) -> None:
    with pytest.raises(ValidationError):
        Applicability(**data)


def test_persisted_approval_cannot_refer_to_different_snapshot() -> None:
    data = payload() | {
        "updated_at": "2026-10-12T00:00:00Z",
        "decisions": [
            {
                "source_release": "26.07d",
                "scope_root": "C3262",
                "support": {"axis_source": "nlp"},
                "actor": "Alice",
                "rationale": "reviewed",
                "lifecycle": "locally-approved",
                "decided_at": "2026-10-12T00:00:00Z",
            }
        ],
        "applicability": {"state": "applicable", "decision_index": 0},
    }
    assert (
        LedgerEntry.model_validate_json(json.dumps(data)).decisions[0].actor == "Alice"
    )
    data["source_release"] = "26.09d"
    with pytest.raises(ValidationError, match="snapshot"):
        LedgerEntry.model_validate_json(json.dumps(data))
    data["applicability"] = {"state": "no-decision", "reason": "none"}
    with pytest.raises(ValidationError, match="history requires"):
        LedgerEntry.model_validate_json(json.dumps(data))


@pytest.mark.parametrize("axis", ["op:Morphology ", "Morphology", "R108\n"])
def test_ledger_rejects_noncanonical_axis_keys(axis: str) -> None:
    with pytest.raises(ValidationError):
        LedgerAssessment.model_validate_json(json.dumps(payload() | {"axis": axis}))


def test_ledger_timestamp_requires_timezone() -> None:
    data = payload() | {
        "updated_at": "2026-10-12T00:00:00",
        "applicability": {"state": "no-decision", "reason": "Unreviewed"},
    }
    with pytest.raises(ValidationError):
        LedgerEntry.model_validate_json(json.dumps(data))
