from pathlib import Path

import pytest

from ontolib.decomposition.r103_review import build_r103_review_packet


@pytest.mark.integration
@pytest.mark.full_store
def test_r103_review_offline_packet_matches_pinned_stated_source() -> None:
    packet = build_r103_review_packet(
        Path("data/ncit-owl/Thesaurus-stated.owl"),
        Path("data/qlever-ncit/.ontoprism-ncit-candidate.json"),
        Path("ontolib/tests/decomposition/golden/proposal-registry.json"),
    )
    assert tuple(
        (row.subject_code, row.role_code, row.filler_code) for row in packet.rows
    ) == (
        ("C2860", "R103", "C12950"),
        ("C3264", "R103", "C12950"),
        ("C3716", "R103", "C34228"),
    )
    assert packet.source_release == "26.07d"
    assert (
        packet.inventory_scope == "issue-declared assertions, source-presence certified"
    )
    assert packet.method_reference.subject_code == "C3708"
    assert packet.method_reference.is_decision_row is False
