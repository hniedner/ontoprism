"""Disposable-QLever contract for isolated showcase activation."""

import os

import pytest

from ontolib.decomposition import vocab
from ontolib.decomposition.enhanced_showcase import (
    SHOWCASE_GRAPH_IRI,
    activate_showcase_decision_graph,
    build_showcase_decision_query,
    load_packaged_showcase_decision_set,
    showcase_staging_graph_iri,
    validate_showcase_rows,
)
from ontolib.terminologies.ncit.client import ncit_sparql_client

pytestmark = [
    pytest.mark.integration,
    pytest.mark.mutating_integration,
    pytest.mark.usefixtures("isolated_qlever_settings"),
]


@pytest.mark.integration
async def test_showcase_activation_replaces_only_its_graph_on_real_qlever() -> None:
    policy = load_packaged_showcase_decision_set()
    staging = showcase_staging_graph_iri("integration-127")
    url = os.environ.get("NCIT_SPARQL_URL", "http://localhost:7888")
    async with ncit_sparql_client(url) as client:
        await activate_showcase_decision_graph(client, run_id="integration-127")
        rows = await client.select(build_showcase_decision_query("C35756"))
        staging_rows = await client.select(
            f"SELECT ?s WHERE {{ GRAPH <{staging}> {{ ?s ?p ?o }} }} LIMIT 1"
        )

    validate_showcase_rows(rows)
    assert len(rows) == len(policy.concept("C35756").decisions)
    assert staging_rows == []
    assert SHOWCASE_GRAPH_IRI != vocab.DECOMPOSED_GRAPH_IRI
