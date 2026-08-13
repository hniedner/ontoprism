import pytest
from pydantic import ValidationError

from ontolib.repositories.icdo.congruence import (
    CongruenceEvidence,
    CongruenceReport,
    CongruenceRow,
)

pytestmark = pytest.mark.unit


def _row(code: str, classification: str) -> CongruenceRow:
    return CongruenceRow(
        code=code,
        classification=classification,
        reason="inspection result",
        candidates=("UBERON:0002048",)
        if classification == "one-supported-candidate"
        else (),
        evidence=(
            CongruenceEvidence(
                kind="normalized-preferred", candidate="UBERON:0002048", value="lung"
            ),
        ),
    )


def test_report_classifies_each_source_code_once_and_reconciles_aggregates() -> None:
    report = CongruenceReport.build(
        icdo_serving_identity="a" * 64,
        uberon_serving_identity="b" * 64,
        source_codes=("C34", "C80.9"),
        rows=(
            _row("C34", "one-supported-candidate"),
            _row("C80.9", "intentionally-unresolved"),
        ),
    )
    assert report.total == 2
    assert report.counts == {
        "one-supported-candidate": 1,
        "intentionally-unresolved": 1,
    }
    assert not hasattr(report.rows[0], "predicate")


def test_report_rejects_duplicate_missing_or_unknown_source_codes() -> None:
    with pytest.raises(ValueError, match="exactly once"):
        CongruenceReport.build(
            icdo_serving_identity="a" * 64,
            uberon_serving_identity="b" * 64,
            source_codes=("C34", "C80.9"),
            rows=(_row("C34", "no-candidate"), _row("C34", "multiple-candidates")),
        )


def test_evidence_kind_cannot_claim_mapping_identity() -> None:
    with pytest.raises(ValidationError):
        CongruenceEvidence(kind="exactMatch", candidate="UBERON:1", value="same")
