"""Client success/error paths against a real local HTTP server stubbing Oxigraph.

Not a mock of our code: a genuine ``http.server`` returns canned SPARQL-JSON over a
real socket, so the client's request/parse/count/version/ask paths are exercised
end-to-end and run in CI without the live NCIt store.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any

import pytest

from ontolib.core.exceptions import StorageError
from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient

if TYPE_CHECKING:
    from collections.abc import Iterator


# Sentinel markers for error-path tests (the handler converts these into the
# specific error response the client must handle).
_COUNT_NONE = "count_none"  # SPARQL-JSON without a "count" binding
_COUNT_BAD = "count_bad"  # SPARQL-JSON with a non-integer count value
_NON_JSON = "non_json"  # response body that is not valid JSON


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
                    "rel": {"value": f"{NCIT_NS}R105"},
                    "target": {"value": f"{NCIT_NS}C12922"},
                }
            ]
        },
    }

    if _COUNT_NONE in query:
        body = {"head": {"vars": ["count"]}, "results": {"bindings": [{}]}}
    elif _COUNT_BAD in query:
        body = {
            "head": {"vars": ["count"]},
            "results": {"bindings": [{"count": {"value": "not_a_number"}}]},
        }
    elif _NON_JSON in query:
        content_type = "text/plain"
        body = "not json at all"
    elif "boom" in query:
        status = 400
        body = {"error": "syntax"}
    elif query.lstrip().upper().startswith("ASK"):
        body = {"head": {}, "boolean": True}
    elif "COUNT" in query:
        body = {
            "head": {"vars": ["count"]},
            "results": {"bindings": [{"count": {"value": "7"}}]},
        }
    elif "versionInfo" in query:
        body = {
            "head": {"vars": ["v"]},
            "results": {"bindings": [{"v": {"value": "26.02d"}}]},
        }

    return status, content_type, body


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        query = self.rfile.read(length).decode("utf-8")
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
    async with OxigraphHttpClient(stub_url) as client:
        assert await client.count() == 7


@pytest.mark.unit
async def test_version_parses_value(stub_url: str) -> None:
    async with OxigraphHttpClient(stub_url) as client:
        assert await client.version() == "26.02d"


@pytest.mark.unit
async def test_ask_returns_boolean(stub_url: str) -> None:
    async with OxigraphHttpClient(stub_url) as client:
        assert await client.ask("ASK { ?s ?p ?o }") is True


@pytest.mark.unit
async def test_select_flattens_rows(stub_url: str) -> None:
    async with OxigraphHttpClient(stub_url) as client:
        rows = await client.select("SELECT ?rel ?target WHERE { ?s ?p ?o }")
    assert rows == [{"rel": f"{NCIT_NS}R105", "target": f"{NCIT_NS}C12922"}]


@pytest.mark.unit
async def test_non_200_status_raises_storage_error(stub_url: str) -> None:
    async with OxigraphHttpClient(stub_url) as client:
        with pytest.raises(StorageError, match="HTTP 400"):
            await client.select("SELECT boom WHERE { ?s ?p ?o }")


@pytest.mark.unit
async def test_count_no_binding_raises_storage_error(stub_url: str) -> None:
    async with OxigraphHttpClient(stub_url) as client:
        with pytest.raises(StorageError, match="no 'count' binding"):
            await client.count(
                "SELECT (COUNT(*) AS ?c) WHERE { ?s ?p ?o }  # count_none"
            )


@pytest.mark.unit
async def test_count_bad_integer_raises_storage_error(stub_url: str) -> None:
    async with OxigraphHttpClient(stub_url) as client:
        with pytest.raises(StorageError, match="did not parse as int"):
            await client.count(
                "SELECT (COUNT(*) AS ?count) WHERE { ?s ?p ?o }  # count_bad"
            )


@pytest.mark.unit
async def test_select_raw_non_json_raises_storage_error(stub_url: str) -> None:
    async with OxigraphHttpClient(stub_url) as client:
        with pytest.raises(StorageError, match="not valid JSON"):
            await client.select_raw("SELECT ?x WHERE { ?s ?p ?o }  # non_json")


@pytest.mark.unit
async def test_endpoint_url_property(stub_url: str) -> None:
    async with OxigraphHttpClient(stub_url) as client:
        assert client.endpoint_url == stub_url.rstrip("/")


@pytest.mark.unit
async def test_load_server_error_raises_storage_error(stub_url: str) -> None:
    async with OxigraphHttpClient(stub_url) as client:
        with pytest.raises(StorageError, match="Store load failed"):
            await client.load(b"<a> <b> <c> .", content_type="text/turtle")
