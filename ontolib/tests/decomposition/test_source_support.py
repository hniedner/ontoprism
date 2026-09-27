"""Source support is a filler count, independent of policy and review decisions."""

from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from scripts import source_backed_fillers
from scripts.source_backed_fillers import headline, summarize_fillers

from ontolib.decomposition.source_support import ConstituentEvidence, StatedFillerSource


@pytest.mark.parametrize(
    "run",
    [
        None,
        SimpleNamespace(
            status="running", publication_state="published", ncit_version="26.07d"
        ),
        SimpleNamespace(
            status="complete", publication_state="pending", ncit_version="26.07d"
        ),
    ],
)
async def test_report_refuses_nonpublished_run_without_success_output(
    monkeypatch, capsys, run
):
    engine = SimpleNamespace(dispose=AsyncMock())
    store = SimpleNamespace(
        get_run=AsyncMock(return_value=run),
        work_item_outcomes=AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        source_backed_fillers, "create_async_engine", lambda *a, **kw: engine
    )
    monkeypatch.setattr(source_backed_fillers, "async_sessionmaker", lambda *a: None)
    monkeypatch.setattr(source_backed_fillers, "ProvenanceStore", lambda *a: store)
    with pytest.raises(ValueError, match="completed published run"):
        await source_backed_fillers.report("run", details=False)
    assert capsys.readouterr().out == ""


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
