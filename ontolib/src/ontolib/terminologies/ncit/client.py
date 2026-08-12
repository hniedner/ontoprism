"""NCIt-specific QLever HTTP client configuration."""

from __future__ import annotations

from ontolib.decomposition.vocab import DECOMPOSED_GRAPH_IRI
from ontolib.repositories.xref.vocab import NCIT_UPSTREAM_XREF_GRAPH_IRI
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI
from ontolib.terminologies.sparql_http_client import SparqlHttpClient

NCIT_NAMED_GRAPHS = (
    STATED_GRAPH_IRI,
    DECOMPOSED_GRAPH_IRI,
    NCIT_UPSTREAM_XREF_GRAPH_IRI,
)


def ncit_sparql_client(
    service_url: str,
    *,
    connect_timeout: float = 5.0,
    query_timeout: float = 30.0,
) -> SparqlHttpClient:
    """Create the NCIt QLever client with its complete isolated graph dataset."""
    return SparqlHttpClient.for_qlever(
        service_url,
        named_graphs=NCIT_NAMED_GRAPHS,
        connect_timeout=connect_timeout,
        query_timeout=query_timeout,
    )
