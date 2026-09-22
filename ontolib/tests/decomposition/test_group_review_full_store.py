from pathlib import Path

import pytest
from scripts.decompose import _source_snapshot
from scripts.research import group_review_packet as group_review


@pytest.mark.integration
@pytest.mark.full_store
async def test_group_review_boundary_matches_configured_authoritative_source(
    tmp_path: Path,
) -> None:
    evidence = Path(
        "ontolib/tests/decomposition/golden/neoplasm-current-engine-evidence.json"
    )
    comparison = Path(
        "ontolib/tests/decomposition/golden/neoplasm-current-comparison.json"
    )
    source = await _source_snapshot(
        Path("data/qlever-ncit/.ontoprism-ncit-candidate.json"),
        "http://localhost:7888",
    )
    packet = group_review.generate_group_review_boundary(
        evidence_path=evidence,
        comparison_path=comparison,
        output=tmp_path / "packet.json",
        workbook=tmp_path / "review.xlsx",
        correction_audit=tmp_path / "audit.xlsx",
        blank_validation=tmp_path / "blank-validation.json",
    )

    assert packet.source_identity == source.source_identity
    assert packet.ncit_version == source.ontology_version
    assert packet.review_rows
    assert packet.cohort.highest_fanout_occurrences > 1
