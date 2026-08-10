"""Store-neutral SPARQL HTTP transport contracts."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import parse_qs, urlsplit

import pytest

from ontolib.terminologies.sparql_http_client import (
    SparqlEndpointProfile,
    SparqlHttpClient,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


class _ProfileHandler(BaseHTTPRequestHandler):
    requests: ClassVar[list[tuple[str, str, dict[str, list[str]], str | None, bytes]]]

    def _record(self, body: bytes) -> None:
        split = urlsplit(self.path)
        type(self).requests.append(
            (
                self.command,
                split.path,
                parse_qs(split.query, keep_blank_values=True),
                self.headers.get("Content-Type"),
                body,
            )
        )

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self._record(body)
        if self.headers.get("Content-Type") == "application/sparql-query":
            payload = json.dumps(
                {"head": {"vars": []}, "results": {"bindings": []}}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/sparql-results+json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_PUT(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self._record(body)
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *_args: Any) -> None:
        pass


@pytest.fixture
def endpoint_origin() -> Iterator[str]:
    _ProfileHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ProfileHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.unit
async def test_profile_routes_each_protocol_operation_to_its_declared_endpoint(
    endpoint_origin: str,
) -> None:
    profile = SparqlEndpointProfile(
        service_url=f"{endpoint_origin}/fuseki/ncit",
        query_url=f"{endpoint_origin}/fuseki/ncit/sparql",
        update_url=f"{endpoint_origin}/fuseki/ncit/update",
        graph_store_url=f"{endpoint_origin}/fuseki/ncit/data",
    )
    async with SparqlHttpClient(profile) as client:
        assert await client.select_once("SELECT * WHERE { FILTER(false) }") == []
        await client.update("INSERT DATA { <urn:s> <urn:p> <urn:o> }")
        await client.load(b"<urn:s> <urn:p> <urn:o> .", content_type="text/turtle")
        await client.load(
            b"<urn:s2> <urn:p> <urn:o> .",
            content_type="text/turtle",
            graph_iri="urn:ontoprism:test",
        )

    assert _ProfileHandler.requests == [
        (
            "POST",
            "/fuseki/ncit/sparql",
            {},
            "application/sparql-query",
            b"SELECT * WHERE { FILTER(false) }",
        ),
        (
            "POST",
            "/fuseki/ncit/update",
            {},
            "application/sparql-update",
            b"INSERT DATA { <urn:s> <urn:p> <urn:o> }",
        ),
        (
            "PUT",
            "/fuseki/ncit/data",
            {"default": [""]},
            "text/turtle",
            b"<urn:s> <urn:p> <urn:o> .",
        ),
        (
            "PUT",
            "/fuseki/ncit/data",
            {"graph": ["urn:ontoprism:test"]},
            "text/turtle",
            b"<urn:s2> <urn:p> <urn:o> .",
        ),
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    "field",
    ["service_url", "query_url", "update_url", "graph_store_url"],
)
def test_profile_rejects_a_non_http_operation_url(field: str) -> None:
    values = {
        "service_url": "http://example.test/service",
        "query_url": "http://example.test/query",
        "update_url": "http://example.test/update",
        "graph_store_url": "http://example.test/store",
    }
    values[field] = "file:///tmp/not-an-http-endpoint"

    with pytest.raises(ValueError, match=field):
        SparqlEndpointProfile(**values)


@pytest.mark.unit
def test_qlever_profile_isolates_default_and_declares_allowed_named_graphs() -> None:
    profile = SparqlEndpointProfile.for_qlever(
        "http://example.test:7001",
        named_graphs=(
            "urn:ontoprism:ncit:stated",
            "urn:ontoprism:ncit:decomposed",
        ),
    )

    query_url = urlsplit(profile.query_url)
    assert f"{query_url.scheme}://{query_url.netloc}{query_url.path}" == (
        "http://example.test:7001/"
    )
    assert parse_qs(query_url.query) == {
        "default-graph-uri": [
            "http://qlever.cs.uni-freiburg.de/builtin-functions/default-graph"
        ],
        "named-graph-uri": [
            "urn:ontoprism:ncit:stated",
            "urn:ontoprism:ncit:decomposed",
        ],
    }
    assert profile.update_url == "http://example.test:7001/"
    assert profile.graph_store_url == "http://example.test:7001/"


@pytest.mark.unit
def test_qlever_profile_admits_constant_graphs_named_by_each_query() -> None:
    """Run-scoped publication graphs remain visible without restoring union reads."""
    profile = SparqlEndpointProfile.for_qlever(
        "http://example.test:7001",
        named_graphs=("urn:ontoprism:ncit:stated",),
    )

    query_url = urlsplit(
        profile.query_url_for(
            "SELECT ?s WHERE { "
            "GRAPH <urn:ontoprism:ncit:decomposed/staging/abc> { ?s ?p ?o } "
            "GRAPH <urn:ontoprism:ncit:stated> { ?s ?p ?o } "
            "}"
        )
    )

    assert parse_qs(query_url.query) == {
        "default-graph-uri": [
            "http://qlever.cs.uni-freiburg.de/builtin-functions/default-graph"
        ],
        "named-graph-uri": [
            "urn:ontoprism:ncit:stated",
            "urn:ontoprism:ncit:decomposed/staging/abc",
        ],
    }
    # A variable graph cannot silently widen the dataset. Only the declared base
    # graph remains available, preserving the isolation contract.
    assert profile.query_url_for("SELECT ?g WHERE { GRAPH ?g { ?s ?p ?o } }") == (
        profile.query_url
    )


@pytest.mark.unit
async def test_qlever_client_dispatches_with_query_named_graphs(
    endpoint_origin: str,
) -> None:
    async with SparqlHttpClient.for_qlever(
        endpoint_origin,
        named_graphs=("urn:ontoprism:ncit:stated",),
    ) as client:
        assert (
            await client.select_once(
                "SELECT ?s WHERE { GRAPH <urn:ontoprism:run:abc> { ?s ?p ?o } }"
            )
            == []
        )

    assert _ProfileHandler.requests == [
        (
            "POST",
            "/",
            {
                "default-graph-uri": [
                    "http://qlever.cs.uni-freiburg.de/builtin-functions/default-graph"
                ],
                "named-graph-uri": [
                    "urn:ontoprism:ncit:stated",
                    "urn:ontoprism:run:abc",
                ],
            },
            "application/sparql-query",
            b"SELECT ?s WHERE { GRAPH <urn:ontoprism:run:abc> { ?s ?p ?o } }",
        )
    ]


@pytest.mark.unit
async def test_qlever_client_normalizes_turtle_collections_before_upload(
    endpoint_origin: str,
) -> None:
    turtle = b"""
        @prefix ex: <urn:example:> .
        @prefix owl: <http://www.w3.org/2002/07/owl#> .
        ex:Class owl:intersectionOf ( ex:Genus ex:Restriction ) .
    """

    async with SparqlHttpClient.for_qlever(
        endpoint_origin,
        named_graphs=(),
    ) as client:
        await client.load(
            turtle,
            content_type="text/turtle",
            graph_iri="urn:ontoprism:test:stated",
        )

    method, path, parameters, content_type, payload = _ProfileHandler.requests[0]
    assert (method, path, parameters, content_type) == (
        "PUT",
        "/",
        {"graph": ["urn:ontoprism:test:stated"]},
        "application/n-triples",
    )
    assert b"http://www.w3.org/1999/02/22-rdf-syntax-ns#first" in payload
    assert b"http://www.w3.org/1999/02/22-rdf-syntax-ns#rest" in payload


@pytest.mark.unit
def test_standard_profile_factory_declares_three_protocol_paths() -> None:
    profile = SparqlEndpointProfile.for_standard_paths("http://example.test/service/")

    assert profile.service_url == "http://example.test/service"
    assert (
        urlsplit(profile.query_url).path,
        urlsplit(profile.update_url).path,
        urlsplit(profile.graph_store_url).path,
    ) == ("/service/query", "/service/update", "/service/store")


@pytest.mark.unit
def test_engine_selection_has_one_validated_qlever_profile() -> None:
    named_graphs = (
        "urn:ontoprism:ncit:stated",
        "urn:ontoprism:ncit:decomposed",
    )

    selected = SparqlEndpointProfile.for_engine(
        "qlever",
        "http://example.test/ncit",
        named_graphs=named_graphs,
    )
    expected = SparqlEndpointProfile.for_qlever(
        "http://example.test/ncit", named_graphs=named_graphs
    )

    assert selected == expected


@pytest.mark.unit
def test_qlever_profile_without_dataset_parameters_keeps_all_named_graphs_visible() -> (
    None
):
    profile = SparqlEndpointProfile.for_qlever(
        "http://example.test:7001",
        named_graphs=None,
    )

    assert profile.query_url == "http://example.test:7001/"


@pytest.mark.unit
def test_engine_selection_rejects_an_unknown_runtime_value() -> None:
    with pytest.raises(ValueError, match="SPARQL engine"):
        SparqlEndpointProfile.for_engine(
            "unknown",
            "http://example.test/ncit",
            named_graphs=(),
        )
