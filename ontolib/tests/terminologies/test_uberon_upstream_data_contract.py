"""Version-pinned shape of the certified Uberon/CL QLever index."""

import pytest

from backend.config import get_settings
from ontolib.terminologies.sparql_http_client import SparqlHttpClient
from ontolib.terminologies.uberon.graph_store import UberonGraphStore

pytestmark = [pytest.mark.integration, pytest.mark.full_store]


@pytest.mark.asyncio
async def test_certified_uberon_cl_index_shape() -> None:
    async with SparqlHttpClient.for_qlever(get_settings().uberon_sparql_url) as client:
        rows = await client.select(
            """PREFIX owl: <http://www.w3.org/2002/07/owl#>
            PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
            SELECT ?shape (COUNT(*) AS ?count) WHERE {
              { SELECT ("uberon" AS ?shape) ?c WHERE {
                  ?c rdfs:label ?label .
                  FILTER(STRSTARTS(STR(?c), "http://purl.obolibrary.org/obo/UBERON_"))
              } }
              UNION { SELECT ("cl" AS ?shape) ?c WHERE {
                  ?c a owl:Class ; rdfs:label ?label .
                  FILTER(STRSTARTS(STR(?c), "http://purl.obolibrary.org/obo/CL_"))
              } }
              UNION { SELECT ("named-subclass" AS ?shape) ?c WHERE {
                  ?c rdfs:subClassOf ?parent . FILTER(isIRI(?parent))
              } }
              UNION { SELECT ("direct-part-of" AS ?shape) ?c WHERE {
                  ?c <http://purl.obolibrary.org/obo/BFO_0000050> ?target
              } }
              UNION { SELECT ("restricted-part-of" AS ?shape) ?c WHERE {
                  ?c rdfs:subClassOf ?restriction .
                  ?restriction owl:onProperty
                    <http://purl.obolibrary.org/obo/BFO_0000050> ;
                    owl:someValuesFrom ?target
              } }
            } GROUP BY ?shape"""
        )

    counts = {row["shape"]: int(row["count"]) for row in rows}
    assert counts | {"direct-part-of": 0} == {
        "uberon": 16_071,
        "cl": 1_484,
        "named-subclass": 35_459,
        "direct-part-of": 0,
        "restricted-part-of": 15_898,
    }


@pytest.mark.asyncio
async def test_restriction_double_matches_real_edge_kind_verdict() -> None:
    class _RestrictionDouble:
        async def select(self, query: str) -> list[dict[str, str]]:
            if "SELECT ?label ?definition" in query:
                return [{"label": "lung"}]
            if "owl:onProperty" in query:
                return [
                    {
                        "rel": "http://purl.obolibrary.org/obo/BFO_0000050",
                        "target": "http://purl.obolibrary.org/obo/UBERON_0000170",
                    },
                    {
                        "rel": "http://purl.obolibrary.org/obo/RO_0002202",
                        "target": "http://purl.obolibrary.org/obo/UBERON_0000118",
                    },
                ]
            return []

    double = await UberonGraphStore(_RestrictionDouble()).get_concept_detail(  # type: ignore[arg-type]
        "UBERON:0002048"
    )
    async with SparqlHttpClient.for_qlever(get_settings().uberon_sparql_url) as client:
        real = await UberonGraphStore(client).get_concept_detail("UBERON:0002048")

    assert double is not None
    assert real is not None
    assert (
        {edge.kind for edge in double.relations}
        == {edge.kind for edge in real.relations}
        == {"part_of", "other-restriction"}
    )
