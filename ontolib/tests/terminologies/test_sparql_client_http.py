"""Client success/error paths against a real local HTTP server stubbing QLever.

Not a mock of our code: a genuine ``http.server`` returns canned SPARQL-JSON over a
real socket, so the client's request/parse/count/version/ask paths are exercised
end-to-end and run in CI without the live NCIt store.
"""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import pytest

from ontolib.core.exceptions import StorageError
from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.sparql_http_client import SparqlHttpClient

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from contextlib import AbstractContextManager


# Sentinel markers for error-path tests (the handler converts these into the
# specific error response the client must handle).
_COUNT_NONE = "count_none"  # SPARQL-JSON without a "count" binding
_COUNT_BAD = "count_bad"  # SPARQL-JSON with a non-integer count value
_NON_JSON = "non_json"  # response body that is not valid JSON
_NON_OBJECT_JSON = "non_object_json"  # valid JSON with the wrong top-level shape
_MISSING_PROJECTION = "missing_projection"
_CLOSE_CONNECTION = "close_connection"
_CLOSE_FIRST_CONNECTION = "close_first_connection"
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _respond_for(query: str) -> tuple[int, str, dict[str, Any] | str]:
    """Return a canned (status, content-type, body) for the query shape under test.

    Returns a (status, content-type, body) tuple — body is either a dict for JSON
    responses or a raw string for non-JSON responses.
    """
    status = 200
    content_type = "application/sparql-results+json"
    body: dict[str, Any] | str = {
        "head": {"vars": ["rel", "target"]},
        "results": {
            "bindings": [
                {
                    "rel": {"type": "uri", "value": f"{NCIT_NS}R105"},
                    "target": {"type": "uri", "value": f"{NCIT_NS}C12922"},
                }
            ]
        },
    }

    if _MISSING_PROJECTION in query:
        body = {"head": {"vars": ["rel"]}, "results": {"bindings": []}}
    elif _COUNT_NONE in query:
        body = {"head": {"vars": ["count"]}, "results": {"bindings": [{}]}}
    elif _COUNT_BAD in query:
        body = {
            "head": {"vars": ["count"]},
            "results": {
                "bindings": [{"count": {"type": "literal", "value": "not_a_number"}}]
            },
        }
    elif _NON_JSON in query:
        content_type = "text/plain"
        body = "not json at all"
    elif _NON_OBJECT_JSON in query:
        body = "[]"
    elif "boom" in query:
        status = 400
        body = {"error": "syntax"}
    elif query.lstrip().upper().startswith("ASK"):
        body = {"head": {}, "boolean": True}
    elif "COUNT" in query:
        body = {
            "head": {"vars": ["count"]},
            "results": {"bindings": [{"count": {"type": "literal", "value": "7"}}]},
        }
    elif "versionInfo" in query:
        body = {
            "head": {"vars": ["v"]},
            "results": {"bindings": [{"v": {"type": "literal", "value": "26.02d"}}]},
        }

    return status, content_type, body


class _Handler(BaseHTTPRequestHandler):
    closed_connection_requests = 0
    close_first_connection_requests = 0
    update_requests: ClassVar[list[tuple[str, str, str | None]]] = []

    def _handle_update(self, update: str) -> None:
        type(self).update_requests.append(
            (self.path, update, self.headers.get("Content-Type"))
        )
        if _CLOSE_CONNECTION in update:
            type(self).closed_connection_requests += 1
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return
        self.send_response(400 if "boom" in update else 204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        query = self.rfile.read(length).decode("utf-8")
        if self.path.endswith("/update"):
            self._handle_update(query)
            return
        if _CLOSE_CONNECTION in query:
            type(self).closed_connection_requests += 1
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return
        if _CLOSE_FIRST_CONNECTION in query:
            type(self).close_first_connection_requests += 1
            if type(self).close_first_connection_requests == 1:
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
        if self.path.startswith("/missing-version/"):
            status, content_type, payload = (
                200,
                "application/sparql-results+json",
                {"head": {"vars": []}, "results": {"bindings": []}},
            )
        elif self.path.startswith("/empty-version/"):
            status, content_type, payload = (
                200,
                "application/sparql-results+json",
                {"head": {"vars": ["v"]}, "results": {"bindings": []}},
            )
        elif self.path.startswith("/unbound-version/"):
            status, content_type, payload = (
                200,
                "application/sparql-results+json",
                {"head": {"vars": ["v"]}, "results": {"bindings": [{}]}},
            )
        elif self.path.startswith("/ambiguous-version/"):
            status, content_type, payload = (
                200,
                "application/sparql-results+json",
                {
                    "head": {"vars": ["v"]},
                    "results": {
                        "bindings": [
                            {"v": {"type": "literal", "value": "26.02d"}},
                            {"v": {"type": "literal", "value": "26.03d"}},
                        ]
                    },
                },
            )
        else:
            status, content_type, payload = _respond_for(query)
        if isinstance(payload, dict):
            body = json.dumps(payload).encode("utf-8")
        else:
            body = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_PUT(self) -> None:
        self.send_response(500)
        self.send_header("Content-Type", "text/plain")
        body = b"internal server error"
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: Any) -> None:
        pass  # silence per-request logging in tests


@pytest.fixture
def stub_url() -> Iterator[str]:
    _Handler.closed_connection_requests = 0
    _Handler.close_first_connection_requests = 0
    _Handler.update_requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
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
async def test_count_parses_integer(stub_url: str) -> None:
    async with SparqlHttpClient(stub_url) as client:
        assert await client.count() == 7


@pytest.mark.unit
async def test_version_parses_value(stub_url: str) -> None:
    async with SparqlHttpClient(stub_url) as client:
        assert await client.version() == "26.02d"


@pytest.mark.unit
async def test_version_rejects_missing_projected_variable(stub_url: str) -> None:
    async with SparqlHttpClient(f"{stub_url}/missing-version") as client:
        with pytest.raises(
            StorageError, match=r"missing required projected variable.*v"
        ):
            await client.version()


@pytest.mark.unit
async def test_version_returns_none_for_valid_empty_result(stub_url: str) -> None:
    async with SparqlHttpClient(f"{stub_url}/empty-version") as client:
        assert await client.version() is None


@pytest.mark.unit
async def test_version_rejects_unbound_result_row(stub_url: str) -> None:
    async with SparqlHttpClient(f"{stub_url}/unbound-version") as client:
        with pytest.raises(StorageError, match="no 'v' binding"):
            await client.version()


@pytest.mark.unit
async def test_version_rejects_ambiguous_values(stub_url: str) -> None:
    async with SparqlHttpClient(f"{stub_url}/ambiguous-version") as client:
        with pytest.raises(StorageError, match="multiple"):
            await client.version()


@pytest.mark.unit
async def test_ask_returns_boolean(stub_url: str) -> None:
    async with SparqlHttpClient(stub_url) as client:
        assert await client.ask("ASK { ?s ?p ?o }") is True


@pytest.mark.unit
async def test_select_flattens_rows(stub_url: str) -> None:
    async with SparqlHttpClient(stub_url) as client:
        rows = await client.select("SELECT ?rel ?target WHERE { ?s ?p ?o }")
    assert rows == [{"rel": f"{NCIT_NS}R105", "target": f"{NCIT_NS}C12922"}]


@pytest.mark.unit
async def test_select_forwards_required_projected_variables(stub_url: str) -> None:
    async with SparqlHttpClient(stub_url) as client:
        with pytest.raises(StorageError, match="target"):
            await client.select(
                f"SELECT ?rel ?target WHERE {{ ?s ?p ?o }} # {_MISSING_PROJECTION}",
                required_variables={"rel", "target"},
            )


@pytest.mark.unit
async def test_select_once_does_not_retry_transport_failure(stub_url: str) -> None:
    async with SparqlHttpClient(stub_url) as client:
        with pytest.raises(StorageError, match="transport error"):
            await client.select_once(f"SELECT ?x WHERE {{}} # {_CLOSE_CONNECTION}")

    assert _Handler.closed_connection_requests == 1


@pytest.mark.unit
async def test_ask_once_does_not_retry_transport_failure(stub_url: str) -> None:
    """Candidate invariants must hold on the first attempt (D47).

    ``ask`` retries, so certifying a store through it would let an intermittently
    answering store pass a gate it did not actually satisfy on demand.
    """
    async with SparqlHttpClient(stub_url) as client:
        with pytest.raises(StorageError, match="transport error"):
            await client.ask_once(f"ASK {{}} # {_CLOSE_CONNECTION}")

    assert _Handler.closed_connection_requests == 1


@pytest.mark.unit
async def test_ask_once_returns_the_boolean_result(stub_url: str) -> None:
    async with SparqlHttpClient(stub_url) as client:
        assert await client.ask_once("ASK { ?s ?p ?o }") is True


@pytest.mark.unit
async def test_select_retries_transport_failure(stub_url: str) -> None:
    async with SparqlHttpClient(stub_url) as client:
        rows = await client.select(
            f"SELECT ?rel ?target WHERE {{ ?s ?p ?o }} # {_CLOSE_FIRST_CONNECTION}"
        )

    assert rows == [{"rel": f"{NCIT_NS}R105", "target": f"{NCIT_NS}C12922"}]
    assert _Handler.close_first_connection_requests == 2


@pytest.mark.unit
async def test_non_200_status_raises_storage_error(stub_url: str) -> None:
    async with SparqlHttpClient(stub_url) as client:
        with pytest.raises(StorageError, match="HTTP 400"):
            await client.select("SELECT boom WHERE { ?s ?p ?o }")


@pytest.mark.unit
async def test_count_no_binding_raises_storage_error(stub_url: str) -> None:
    async with SparqlHttpClient(stub_url) as client:
        with pytest.raises(StorageError, match="no 'count' binding"):
            await client.count(
                "SELECT (COUNT(*) AS ?c) WHERE { ?s ?p ?o }  # count_none"
            )


@pytest.mark.unit
async def test_count_bad_integer_raises_storage_error(stub_url: str) -> None:
    async with SparqlHttpClient(stub_url) as client:
        with pytest.raises(StorageError, match="did not parse as int"):
            await client.count(
                "SELECT (COUNT(*) AS ?count) WHERE { ?s ?p ?o }  # count_bad"
            )


@pytest.mark.unit
async def test_select_raw_non_json_raises_storage_error(stub_url: str) -> None:
    async with SparqlHttpClient(stub_url) as client:
        with pytest.raises(StorageError, match="not valid JSON"):
            await client.select_raw("SELECT ?x WHERE { ?s ?p ?o }  # non_json")


@pytest.mark.unit
async def test_select_raw_non_object_json_raises_storage_error(stub_url: str) -> None:
    async with SparqlHttpClient(stub_url) as client:
        with pytest.raises(StorageError, match="root was not an object"):
            await client.select_raw("SELECT ?x WHERE { ?s ?p ?o }  # non_object_json")


@pytest.mark.unit
async def test_endpoint_url_property(stub_url: str) -> None:
    async with SparqlHttpClient(stub_url) as client:
        assert client.endpoint_url == stub_url.rstrip("/")


@pytest.mark.unit
async def test_load_server_error_raises_storage_error(stub_url: str) -> None:
    async with SparqlHttpClient(stub_url) as client:
        with pytest.raises(StorageError, match="Store load failed"):
            await client.load(b"<a> <b> <c> .", content_type="text/turtle")


@pytest.mark.unit
async def test_update_posts_sparql_update_to_the_update_endpoint(
    stub_url: str,
) -> None:
    update = "CLEAR GRAPH <urn:public>"

    async with SparqlHttpClient(stub_url) as client:
        await client.update(update)

    assert _Handler.update_requests == [
        ("/update", update, "application/sparql-update")
    ]


@pytest.mark.unit
async def test_update_rejects_http_error_without_replay(stub_url: str) -> None:
    async with SparqlHttpClient(stub_url) as client:
        with pytest.raises(StorageError, match="SPARQL update failed: HTTP 400"):
            await client.update("CLEAR GRAPH <urn:public> # boom")

    assert len(_Handler.update_requests) == 1


@pytest.mark.unit
async def test_update_does_not_replay_ambiguous_transport_failure(
    stub_url: str,
) -> None:
    async with SparqlHttpClient(stub_url) as client:
        with pytest.raises(StorageError, match="update transport error"):
            await client.update(f"CLEAR GRAPH <urn:public> # {_CLOSE_CONNECTION}")

    assert _Handler.closed_connection_requests == 1
    assert len(_Handler.update_requests) == 1


@pytest.mark.integration
@pytest.mark.mutating_integration
async def test_version_cardinality_verdict_matches_real_qlever_and_http_double(
    stub_url: str,
    isolated_qlever_url: str,
    integration_connection_scope: Callable[[str], AbstractContextManager[None]],
) -> None:
    query = (
        "PREFIX owl: <http://www.w3.org/2002/07/owl#> "
        "SELECT DISTINCT ?v WHERE { "
        "?ont a owl:Ontology ; owl:versionInfo ?v } LIMIT 2"
    )
    async with SparqlHttpClient.for_qlever(
        isolated_qlever_url, named_graphs=()
    ) as real_client:
        assert await real_client.select(query, required_variables={"v"}) == [
            {"v": "26.07d"}
        ]
        second_version = (
            b"@prefix owl: <http://www.w3.org/2002/07/owl#> . "
            b"<urn:ontoprism:test:second-ontology> a owl:Ontology ; "
            b'owl:versionInfo "26.03d" .'
        )
        await real_client.load(
            second_version,
            content_type="text/turtle",
            replace=False,
        )
        try:
            with pytest.raises(StorageError, match="multiple") as real_error:
                await real_client.version()
        finally:
            seed = (_REPO_ROOT / "scripts/ci/fixtures/ncit-fixture.ttl").read_bytes()
            await real_client.load(seed, content_type="text/turtle", replace=True)

    with integration_connection_scope(stub_url):
        async with SparqlHttpClient(f"{stub_url}/ambiguous-version") as double_client:
            with pytest.raises(StorageError, match="multiple") as double_error:
                await double_client.version()

    assert str(real_error.value) == str(double_error.value)
