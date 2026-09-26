"""Source support is a filler count, independent of policy and review decisions."""

from collections import Counter

from scripts.source_backed_fillers import headline, summarize_fillers

from ontolib.decomposition.source_support import ConstituentEvidence, StatedFillerSource


def test_headline_counts_fillers_not_citations_or_policy_choices() -> None:
    source = StatedFillerSource(
        fact_id="fact",
        kind="restriction",
        anchor_code="C1",
        group_id="group",
        depth=0,
        role_code="R101",
        filler_code="C2",
        occurrence_id="occurrence",
        structural_path=[0],
    )
    role = ConstituentEvidence(
        run_id="run",
        concept_code="C1",
        axis="op:PrimarySite",
        filler_code="C2",
        axis_source="role",
        sources=[source, source],
        policy_choices=["axis-assignment", "collapse", "grouping"],
    )
    genus = role.model_copy(
        update={
            "axis_source": "parent",
            "axis": "op:Morphology",
            "sources": [source.model_copy(update={"kind": "genus", "role_code": None})],
        }
    )
    inferred = role.model_copy(update={"axis_source": "nlp", "sources": []})
    counts: Counter[tuple[str, ...]] = Counter()
    summarize_fillers([role, genus, inferred], counts)
    assert headline(counts) == (
        "source-backed filler share: restriction-backed: 1/3 (33.333333%) / "
        "genus-backed: 1/3 (33.333333%) / not-source-backed: 1/3 (33.333333%)"
    )
    assert counts[("op:PrimarySite", "role", "restriction-backed")] == 1
    assert "not-computed" in headline(Counter())
