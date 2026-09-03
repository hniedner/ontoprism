"""Read-only NCIt 26.07d source-shape contract for the C35756 correction."""

import os

import pytest

from ontolib.terminologies.namespaces import NCIT_NS, OWL_NS, RDFS_NS
from ontolib.terminologies.ncit.client import ncit_sparql_client
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI


@pytest.mark.integration
@pytest.mark.full_store
async def test_c9432_exists_with_bound_parents_but_is_not_c35756_source_finding() -> (
    None
):
    url = os.environ.get("NCIT_SPARQL_URL", "http://localhost:7888")
    async with ncit_sparql_client(url, query_timeout=120.0) as client:
        versions = await client.select(
            "PREFIX owl: <http://www.w3.org/2002/07/owl#> "
            f"SELECT ?version WHERE {{ GRAPH <{STATED_GRAPH_IRI}> {{ "
            "?ontology a owl:Ontology ; owl:versionInfo ?version . } } LIMIT 2"
        )
        parents = await client.select(
            f"""SELECT ?parent WHERE {{ GRAPH <{STATED_GRAPH_IRI}> {{
                <{NCIT_NS}C9432> <{RDFS_NS}subClassOf> ?parent .
                FILTER(isIRI(?parent))
            }} }}"""
        )
        findings = await client.select(
            f"""SELECT ?filler WHERE {{ GRAPH <{STATED_GRAPH_IRI}> {{
                <{NCIT_NS}C35756> <{OWL_NS}equivalentClass> ?definition .
                ?definition <{OWL_NS}intersectionOf>/
                    <http://www.w3.org/1999/02/22-rdf-syntax-ns#rest>*/
                    <http://www.w3.org/1999/02/22-rdf-syntax-ns#first>
                    ?restriction .
                ?restriction <{OWL_NS}onProperty> <{NCIT_NS}R108> ;
                    <{OWL_NS}someValuesFrom> ?filler .
            }} }}"""
        )

    assert versions == [{"version": "26.07d"}]
    assert {row["parent"] for row in parents} >= {
        f"{NCIT_NS}C3331",
        f"{NCIT_NS}C198611",
    }
    assert {row["filler"] for row in findings} == {f"{NCIT_NS}C3331"}
