"""Source-support evidence preserves one coherent backing kind per filler."""

import pytest

from ontolib.decomposition.source_support import ConstituentEvidence, StatedFillerSource


def test_mixed_source_kinds_are_not_reported_as_one_support_kind():
    sources = [
        StatedFillerSource(
            fact_id="fact",
            kind=kind,
            anchor_code="C1",
            group_id="group",
            depth=0,
            role_code="R101",
            filler_code="C2",
            occurrence_id="occ",
            structural_path=[0],
        )
        for kind in ("restriction", "genus")
    ]
    with pytest.raises(ValueError, match="mix restriction and genus"):
        ConstituentEvidence(
            run_id="run",
            concept_code="C1",
            axis="op:PrimarySite",
            filler_code="C2",
            axis_source="role",
            sources=sources,
            policy_choices=[],
        )
