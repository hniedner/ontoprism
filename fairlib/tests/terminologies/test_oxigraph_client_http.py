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

from fairlib.core.exceptions import StorageError
from fairlib.terminologies.namespaces import NCIT_NS
from fairlib.terminologies.oxigraph_http_client import OxigraphHttpClient

if TYPE_CHECKING:
    from collections.abc import Iterator


def _respond_for(query: str) -> tuple[int, dict[str, Any]]:
    """Return a canned (status, SPARQL-JSON) for the query shape under test."""
    if "boom" in query:
        return 400, {"error": "syntax"}
    if query.lstrip().upper().startswith("ASK"):
        return 200, {"head": {}, "boolean": True}
    if "COUNT" in query:
        return 200, {
            "head": {"vars": ["count"]},
            "results": {"bindings": [{"count": {"value": "7"}}]},
        }
    if "versionInfo" in query:
        return 200, {
            "head": {"vars": ["v"]},
            "results": {"bindings": [{"v": {"value": "26.02d"}}]},
        }
    return 200, {
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


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        query = self.rfile.read(length).decode("utf-8")
        status, payload = _respond_for(query)
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/sparql-results+json")
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
