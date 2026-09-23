from __future__ import annotations

from ontolib.decomposition import vocab
from ontolib.decomposition.read import decomposition_from_rows


def test_published_concept_api_metadata_keeps_outcome_and_flag_reasons() -> None:
    rows = [
        {
            "publicationStatus": "provisional",
            "publicationNotice": "expert review, not an NCIt release",
            "outcome": "residual",
            "outcomeReason": "decomposition candidate yielded no constituents",
            "flagKind": "unresolved-r101-loss",
            "flagReason": "R101 occurrence abc is unresolved: missing-disposition",
        }
    ]

    result = decomposition_from_rows("C1", rows)

    assert result.publication_status == "provisional"
    assert result.publication_notice == "expert review, not an NCIt release"
    assert result.outcome == "residual"
    assert result.outcome_reason == "decomposition candidate yielded no constituents"
    assert result.review_flags[0].model_dump() == {
        "kind": "unresolved-r101-loss",
        "reason": "R101 occurrence abc is unresolved: missing-disposition",
    }
    assert vocab.CONCEPT_OUTCOME.endswith("conceptOutcome")
