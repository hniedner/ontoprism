"""Nested positive site existentials count as site-bearing; disjunctions do not."""

import pytest
from rdflib import Dataset, URIRef

from ontolib.decomposition.morphology_evidence import read_site_carriers
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI

pytestmark = pytest.mark.unit


# RDFLib's query() still calls its own deprecated Dataset.default_context accessor.
@pytest.mark.filterwarnings(
    "ignore:Dataset.default_context is deprecated:DeprecationWarning"
)
async def test_site_reader_uses_stated_positive_existentials_in_intersections():
    graph = Dataset()
    graph.graph(URIRef(STATED_GRAPH_IRI)).parse(
        data="""@prefix n: <http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#> .
      @prefix owl: <http://www.w3.org/2002/07/owl#> .
      @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        n:C1 rdfs:subClassOf [owl:onProperty n:R100; owl:someValuesFrom n:C9] .
        n:C2 owl:equivalentClass [owl:intersectionOf (n:C8
          [owl:intersectionOf ([owl:onProperty n:R101; owl:someValuesFrom n:C9])])] .
        n:C3 rdfs:subClassOf [owl:onProperty n:R102; owl:someValuesFrom n:C9] .
        n:C4 rdfs:subClassOf [owl:onProperty n:R135; owl:someValuesFrom n:C9] .
        n:C5 owl:equivalentClass [owl:unionOf (n:C8
          [owl:onProperty n:R101; owl:someValuesFrom n:C9])] .
        n:C6 n:R101 n:C9 .
    """,
        format="turtle",
    )
    graph.graph(URIRef("urn:inferred")).parse(
        data="""
        @prefix n: <http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#> .
        @prefix owl: <http://www.w3.org/2002/07/owl#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        n:C7 rdfs:subClassOf [owl:onProperty n:R101; owl:someValuesFrom n:C9] .
    """,
        format="turtle",
    )

    class Client:
        async def select_once(self, query, **kwargs):
            return [{"carrier": str(row[0])} for row in graph.query(query)]  # type: ignore[index]

    assert await read_site_carriers(Client()) == frozenset({"C1", "C2", "C3"})
