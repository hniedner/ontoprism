"""Integration test for the decomposition read path against disposable Oxigraph.

Seeds a tiny ``op:`` graph into DECOMPOSED_GRAPH_IRI and reads it back through the real
query + assembly — validating the read layer end-to-end without the (not-yet-built)
writer. The required disposable store failing to start is a test failure.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from ontolib.decomposition import vocab
from ontolib.decomposition.legacy_writer import write_ttl
from ontolib.decomposition.models import (
    CompleteDefinition,
    Constituent,
    Decomposition,
    RestrictionDefinitionFact,
)
from ontolib.decomposition.read import decomposition_from_rows
from ontolib.decomposition.read_queries import build_decomposition_query
from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient

if TYPE_CHECKING:
    from pathlib import Path

_DEFAULT_NCIT_URL = "http://localhost:7888"
_NCIT = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#"

pytestmark = [
    pytest.mark.mutating_integration,
    pytest.mark.usefixtures("isolated_oxigraph_settings"),
]

# A hand-written decomposed-graph fixture (what the engine will emit for C6135).
_SEED_TTL = f"""
@prefix op: <{vocab.ONTOPRISM_NS}> .
@prefix ncit: <{_NCIT}> .
ncit:C6135 op:representationStatus "{vocab.LEGACY_PRECOORDINATED}" ;
    op:decomposedOn "2026-07-06" ;
    op:hasConstituent
        [ op:axis ncit:R88 ; op:filler ncit:C27970 ;
          op:axisSource "role" ; op:mostSpecific false ] ,
        [ op:axis ncit:R101 ; op:filler ncit:C12400 ;
          op:axisSource "role" ; op:mostSpecific true ] .
""".encode()


def _url() -> str:
    return os.environ.get("NCIT_SPARQL_URL", _DEFAULT_NCIT_URL)


@pytest.mark.integration
async def test_decomposition_round_trips_through_the_decomposed_graph() -> None:
    url = _url()
    async with OxigraphHttpClient(url) as client:
        # Seed the decomposed graph (replace=True isolates the test to its own graph).
        await client.load(
            _SEED_TTL,
            content_type="text/turtle",
            graph_iri=vocab.DECOMPOSED_GRAPH_IRI,
            replace=True,
        )
        rows = await client.select(build_decomposition_query("C6135"))

    decomposition = decomposition_from_rows("C6135", rows)
    assert decomposition.is_legacy_precoordinated is True
    assert decomposition.decomposed_on == "2026-07-06"
    by_axis = {c.axis: c for c in decomposition.constituents}
    assert set(by_axis) == {"R88", "R101"}
    assert by_axis["R101"].filler == "C12400"
    assert by_axis["R101"].most_specific is True
    assert by_axis["R88"].most_specific is False


@pytest.mark.integration
async def test_writer_projection_trace_round_trips_through_real_oxigraph(
    tmp_path: Path,
) -> None:
    fact_id = "a" * 64
    group_id = "b" * 64
    expected = Decomposition(
        code="C6135",
        semantic_type="Neoplastic Process",
        constituents=[
            Constituent(
                axis="op:PrimarySite",
                filler_code="C12400",
                axis_source="role",
                source_role="R101",
                most_specific=True,
                needs_review=True,
                group="anatomy-1",
                source_definition_ids=(fact_id,),
            )
        ],
        complete_definition=CompleteDefinition(
            root_code="C6135",
            facts=(
                RestrictionDefinitionFact(
                    fact_id=fact_id,
                    anchor_code="C6135",
                    group_id=group_id,
                    depth=0,
                    role_code="R101",
                    filler_code="C12400",
                ),
            ),
        ),
    )
    artifact = tmp_path / "complete.ttl"
    await write_ttl([expected], dest=artifact)

    async with OxigraphHttpClient(_url()) as client:
        await client.load(
            artifact.read_bytes(),
            content_type="text/turtle",
            graph_iri=vocab.DECOMPOSED_GRAPH_IRI,
            replace=True,
        )
        rows = await client.select(build_decomposition_query("C6135"))
        fact_rows = await client.select(
            "SELECT ?fact WHERE { "
            f"GRAPH <{vocab.DECOMPOSED_GRAPH_IRI}> {{ "
            f"<{_NCIT}C6135> <{vocab.HAS_DEFINITION_FACT}> ?fact "
            "} }"
        )

    actual = decomposition_from_rows("C6135", rows)
    assert actual.constituents[0].axis == "op:PrimarySite"
    assert actual.constituents[0].source_role == "R101"
    assert actual.constituents[0].group == "anatomy-1"
    assert actual.constituents[0].needs_review is True
    assert actual.constituents[0].source_definition_ids == (fact_id,)
    assert fact_rows == [{"fact": f"{vocab.DEFINITION_FACT_NS}C6135/{fact_id}"}]
