from __future__ import annotations

import uuid

import pytest
from test_support.qlever_graph import (
    preserve_qlever_graph,
    qlever_graph_count,
    qlever_update,
)


@pytest.mark.integration
@pytest.mark.mutating_integration
def test_graph_preservation_restores_disposable_qlever_exactly(
    isolated_qlever_url: str,
) -> None:
    graph = f"urn:ontoprism:test:preservation:{uuid.uuid4().hex}"
    assert qlever_graph_count(isolated_qlever_url, graph) == 0

    with preserve_qlever_graph(isolated_qlever_url, graph):
        qlever_update(
            isolated_qlever_url,
            f"INSERT DATA {{ GRAPH <{graph}> {{ <urn:s> <urn:p> <urn:o> }} }}",
        )
        assert qlever_graph_count(isolated_qlever_url, graph) == 1

    assert qlever_graph_count(isolated_qlever_url, graph) == 0
