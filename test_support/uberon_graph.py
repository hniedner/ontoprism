"""Small explicit source graph shared by corroboration contract tests."""

from rdflib import Graph
from rdflib.query import ResultRow


class GraphClient:
    """Execute production SPARQL over OWL statements, not query mocks."""

    def __init__(self, extra=""):
        self.graph = Graph().parse(
            data="""
            @prefix obo: <http://purl.obolibrary.org/obo/> .
            @prefix owl: <http://www.w3.org/2002/07/owl#> .
            @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
            @prefix oio: <http://www.geneontology.org/formats/oboInOwl#> .
            <http://purl.obolibrary.org/obo/uberon.owl> owl:versionIRI
                <http://purl.obolibrary.org/obo/uberon/releases/2026-06-19/uberon.owl> .
            obo:UBERON_0002048 oio:hasDbXref "NCIT:C12468" ;
                rdfs:subClassOf obo:UBERON_0000062 .
            obo:UBERON_0000479 owl:disjointWith obo:UBERON_0000062 .
            obo:UBERON_0000344 oio:hasDbXref "NCIT:C12470" ;
                rdfs:subClassOf obo:UBERON_0000479 .
            """
            + extra,
            format="turtle",
        )

    async def select(self, query):
        result = []
        for row in self.graph.query(query):
            if not isinstance(row, ResultRow):
                raise TypeError("Expected a SELECT row")
            result.append({str(key): str(value) for key, value in row.asdict().items()})
        return result
