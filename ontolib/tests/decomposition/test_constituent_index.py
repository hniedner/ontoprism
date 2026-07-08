"""Unit tests for constituent_index — resolving NLP aspects (design §7.2)."""

from __future__ import annotations

import pytest

from ontolib.decomposition.constituent_index import resolve_aspects
from ontolib.decomposition.models import Constituent
from ontolib.decomposition.nlp_fallback import AspectRecord


def _lookup(table: dict[str, str]):  # type: ignore[no-untyped-def]
    async def _inner(term: str) -> str | None:
        return table.get(term)

    return _inner


@pytest.mark.unit
async def test_positive_aspect_resolves_to_existing_concept() -> None:
    aspects = [AspectRecord(axis="op:Laterality", surface_form="Left")]
    constituents, minted = await resolve_aspects(aspects, _lookup({"Left": "C12345"}))
    assert constituents == [
        Constituent(axis="op:Laterality", filler_code="C12345", axis_source="nlp"),
    ]
    assert minted == []


@pytest.mark.unit
async def test_positive_aspect_with_no_match_mints_a_proposal() -> None:
    aspects = [AspectRecord(axis="op:Laterality", surface_form="Left")]
    constituents, minted = await resolve_aspects(aspects, _lookup({}))
    assert len(minted) == 1
    assert minted[0].label == "Left"
    assert minted[0].axis == "op:Laterality"
    assert constituents[0].filler_code == minted[0].id
    assert constituents[0].axis_source == "nlp"


@pytest.mark.unit
async def test_negative_polarity_always_mints_never_resolves() -> None:
    # "without Pleural Effusion" — the bare finding concept exists, but that concept
    # does not represent the *negation*; design §7.2 mints the negated phrase itself.
    aspects = [
        AspectRecord(
            axis="op:WithFinding", surface_form="Pleural Effusion", polarity="negative"
        )
    ]
    constituents, minted = await resolve_aspects(
        aspects, _lookup({"Pleural Effusion": "C99999"})
    )
    assert len(minted) == 1
    assert minted[0].label == "Without Pleural Effusion"
    assert constituents[0].filler_code == minted[0].id


@pytest.mark.unit
async def test_positive_with_finding_resolves_when_matched() -> None:
    aspects = [
        AspectRecord(
            axis="op:WithFinding", surface_form="Pleural Effusion", polarity="positive"
        )
    ]
    constituents, minted = await resolve_aspects(
        aspects, _lookup({"Pleural Effusion": "C99999"})
    )
    assert minted == []
    assert constituents[0].filler_code == "C99999"


@pytest.mark.unit
async def test_empty_aspects_returns_empty() -> None:
    constituents, minted = await resolve_aspects([], _lookup({}))
    assert constituents == []
    assert minted == []


@pytest.mark.unit
async def test_multiple_aspects_each_resolved_independently() -> None:
    aspects = [
        AspectRecord(axis="op:Laterality", surface_form="Left"),
        AspectRecord(axis="op:StageSystem", surface_form="AJCC v7"),
    ]
    constituents, minted = await resolve_aspects(aspects, _lookup({"Left": "C1"}))
    assert len(constituents) == 2
    assert len(minted) == 1  # only "AJCC v7" was unresolved
    assert minted[0].label == "AJCC v7"


@pytest.mark.unit
async def test_source_signal_records_the_surface_form() -> None:
    aspects = [AspectRecord(axis="op:Laterality", surface_form="Left")]
    _, minted = await resolve_aspects(aspects, _lookup({}))
    assert minted[0].source_signal == "Left"
