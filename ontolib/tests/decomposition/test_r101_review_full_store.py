from __future__ import annotations

import math
from pathlib import Path

import pytest
from scripts.decompose import _source_snapshot

from ontolib.decomposition.r101_conservation import load_r101_conservation_report
from ontolib.decomposition.r101_review import (
    QLeverReviewLabels,
    build_r101_review_packet,
    write_r101_review_workbook,
)
from ontolib.terminologies.ncit.client import ncit_sparql_client


class _RecordedLabels:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values
        self.requested_codes: tuple[str, ...] = ()

    async def labels_for_review(
        self, codes: tuple[str, ...]
    ) -> dict[str, tuple[str, ...]]:
        self.requested_codes = codes
        return {code: (self.values[code],) for code in codes}


@pytest.mark.integration
@pytest.mark.full_store
async def test_r101_review_labels_match_real_qlever_in_bounded_batches(
    tmp_path: Path,
) -> None:
    report = load_r101_conservation_report(
        Path("ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz")
    )
    manifest_path = Path("data/qlever-ncit/.ontoprism-ncit-candidate.json")
    source = await _source_snapshot(manifest_path, "http://localhost:7888")
    assert source.source_identity == report.source_identity
    assert source.ontology_version == report.source_release_id
    async with ncit_sparql_client("http://localhost:7888") as client:
        labels = QLeverReviewLabels(client)
        packet = await build_r101_review_packet(report, manifest_path, labels)

    assert labels.query_count == math.ceil(len(labels.requested_codes) / 500)
    assert len(labels.requested_codes) > 1900
    assert len(packet.patterns) == 162
    assert len(packet.disease_propositions) == 2800
    assert len(packet.occurrences) == 3291
    assert max(row.occurrence_count for row in packet.patterns) == 245
    assert max(row.occurrence_count for row in packet.disease_propositions) == 3
    assert {row.disease_code for row in packet.disease_propositions} <= set(
        labels.requested_codes
    )
    by_code = (
        {row.broader_code: row.broader_label for row in packet.patterns}
        | {row.retained_code: row.retained_label for row in packet.patterns}
        | {row.disease_code: row.disease_label for row in packet.disease_propositions}
    )
    assert by_code["C12727"] == "Heart"
    assert by_code["C12869"] == "Left Atrium"

    delivered_workbook = tmp_path / "r101-review-workbook-v3.xlsx"
    write_r101_review_workbook(delivered_workbook, packet)
    assert delivered_workbook.stat().st_size > 0

    for pattern in packet.patterns:
        for path in pattern.paths:
            by_code.update(zip(path.code_path, path.labels, strict=True))
    replay_labels = _RecordedLabels(by_code)
    replay = await build_r101_review_packet(report, manifest_path, replay_labels)
    assert replay == packet
    assert replay_labels.requested_codes == labels.requested_codes
