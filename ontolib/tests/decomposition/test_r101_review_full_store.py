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
        self.requested_codes: tuple[str, ...] = ()

    async def labels_for_review(
        self, codes: tuple[str, ...]
    ) -> dict[str, tuple[str, ...]]:
        self.requested_codes = codes
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
    assert len(labels.requested_codes) == 141
    assert packet.packet_identity == (
        "fb3d49cb781a35cabf8df4a10dfcdde3f2d7fc0901268178fb86cfd3235bf090"
    )
    delivered_workbook = tmp_path / "r101-review-workbook.xlsx"
    write_r101_review_workbook(delivered_workbook, packet)
    assert hashlib.sha256(delivered_workbook.read_bytes()).hexdigest() == (
        "96d4152aa89bfcd01489a9dfdf635bdcd9827d2e471200b093ecd103d7a621c8"
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
    expected_sentinels = {
        "C6135": "Stage III Thyroid Gland Medullary Carcinoma AJCC v7",
        "C101539": (
            "Stage I Differentiated Thyroid Gland Carcinoma Under 45 Years AJCC v7"
        ),
        "C4791": "Left Atrial Myxoma",
    }
    assert {
        "C6135": packet.sentinel_labels.c6135,
        "C101539": packet.sentinel_labels.c101539,
        "C4791": packet.sentinel_labels.c4791,
    } == expected_sentinels
    recorded = dict(by_code)
    for pattern in packet.patterns:
        for path in pattern.paths:
            recorded.update(zip(path.code_path, path.labels, strict=True))
    recorded.update(expected_sentinels)
    replay_labels = _RecordedLabels(recorded)
    replay = await build_r101_review_packet(
        report,
        manifest_path,
        replay_labels,
    )
    assert replay == packet
    assert replay_labels.requested_codes == labels.requested_codes
