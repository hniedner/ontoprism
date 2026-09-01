"""Disposable-QLever contract for isolated showcase activation."""

import os

import pytest

from ontolib.decomposition import vocab
from ontolib.decomposition.enhanced_showcase import (
    SHOWCASE_GRAPH_IRI,
    build_showcase_decision_query,
    load_packaged_showcase_decision_set,
    validate_showcase_rows,
)
from ontolib.decomposition.showcase_readiness import activate_showcase_readiness
from ontolib.terminologies.ncit.client import ncit_sparql_client

pytestmark = [
    pytest.mark.integration,
    pytest.mark.mutating_integration,
    pytest.mark.usefixtures("isolated_qlever_settings"),
]


def _count_query(graph: str) -> str:
    return f"SELECT (COUNT(*) AS ?count) WHERE {{ GRAPH <{graph}> {{ ?s ?p ?o }} }}"


@pytest.mark.integration
async def test_showcase_activation_replaces_only_its_graph_on_real_qlever(
    tmp_path,
) -> None:
    policy = load_packaged_showcase_decision_set()
    url = os.environ.get("NCIT_SPARQL_URL", "http://localhost:7888")
    async with ncit_sparql_client(url) as client:
        protected_graphs = (
            vocab.DECOMPOSED_GRAPH_IRI,
            "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus-stated.owl",
            "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus-inferred.owl",
        )
        await client.update(
            "INSERT DATA { GRAPH "
            "<http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus-stated.owl> { "
            "<urn:showcase:ontology> a <http://www.w3.org/2002/07/owl#Ontology> ; "
            '<http://www.w3.org/2002/07/owl#versionInfo> "26.07d" . } }'
        )
        for index, graph in enumerate(protected_graphs):
            await client.update(
                f"INSERT DATA {{ GRAPH <{graph}> {{ "
                f"<urn:showcase:s{index}> <urn:showcase:p> <urn:showcase:o> }} }}"
            )
        before = [
            await client.select(_count_query(graph)) for graph in protected_graphs
        ]
        await activate_showcase_readiness(
            client,
            output=tmp_path / "m1-6-enhanced-showcase-readiness.json",
            git_head="a" * 40,
            producing_command="pdm run agent-replay activate-enhanced-ncit-showcase",
        )
        rows = await client.select(build_showcase_decision_query("C35756"))
        staging_rows = await client.select(
            f"SELECT ?s WHERE {{ GRAPH ?graph {{ ?s ?p ?o }} "
            "FILTER(STRSTARTS(STR(?graph), "
            f'"{SHOWCASE_GRAPH_IRI}/staging/")) }} LIMIT 1'
        )
        after = [await client.select(_count_query(graph)) for graph in protected_graphs]

    validate_showcase_rows(rows)
    assert len(rows) == len(policy.concept("C35756").decisions)
    assert staging_rows == []
    assert before == after
    assert SHOWCASE_GRAPH_IRI != vocab.DECOMPOSED_GRAPH_IRI
