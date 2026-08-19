from __future__ import annotations

import hashlib
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

    async def labels_for_review(
        self, codes: tuple[str, ...]
    ) -> dict[str, tuple[str, ...]]:
        return {code: (self.values[code],) for code in codes}


@pytest.mark.integration
@pytest.mark.full_store
async def test_r101_review_labels_match_real_qlever_in_one_read(tmp_path) -> None:
    report = load_r101_conservation_report(
        Path("ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz")
    )
    manifest_path = Path("data/qlever-ncit/.ontoprism-ncit-candidate.json")
    source = await _source_snapshot(manifest_path, "http://localhost:7888")
    assert source.source_identity == report.source_identity
    assert source.ontology_version == report.source_release_id
    async with ncit_sparql_client("http://localhost:7888") as client:
        labels = QLeverReviewLabels(client)
        packet = await build_r101_review_packet(
            report,
            manifest_path,
            labels,
        )

    assert labels.query_count == 1
    assert packet.packet_identity == (
        "430636bea8ec173f4f95e74ebc33ea0719ab59aa895a5c7f1eab5198dea5b2d8"
    )
    delivered_workbook = tmp_path / "r101-review-workbook.xlsx"
    write_r101_review_workbook(delivered_workbook, packet)
    assert hashlib.sha256(delivered_workbook.read_bytes()).hexdigest() == (
        "8ace596dd33c0ecdc140982902589d670c163a9209797a2552a86c38c35343bb"
    )
    assert len(packet.patterns) == 162
    by_code = {
        row.old_broader_code: row.old_broader_label for row in packet.patterns
    } | {
        row.retained_narrower_code: row.retained_narrower_label
        for row in packet.patterns
    }
    assert by_code["C12727"] == "Heart"
    assert by_code["C12869"] == "Left Atrium"
    recorded = dict(by_code)
    for pattern in packet.patterns:
        for path in pattern.paths:
            recorded.update(zip(path.code_path, path.labels, strict=True))
    replay = await build_r101_review_packet(
        report,
        manifest_path,
        _RecordedLabels(recorded),
    )
    assert replay == packet
