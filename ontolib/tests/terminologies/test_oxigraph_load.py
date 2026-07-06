"""OxigraphHttpClient.load against a real local Graph-Store-Protocol stub server."""

from __future__ import annotations

import io
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any

import pytest

from ontolib.core.exceptions import StorageError
from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient

if TYPE_CHECKING:
    from collections.abc import Iterator

_received: dict[str, Any] = {}


class _StoreHandler(BaseHTTPRequestHandler):
    def _read_body(self) -> bytes:
        # Support both Content-Length and chunked transfer-encoding (streamed uploads).
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            chunks: list[bytes] = []
            while True:
                size = int(self.rfile.readline().split(b";")[0], 16)
                if size == 0:
                    self.rfile.readline()  # consume the trailing CRLF
                    break
                chunks.append(self.rfile.read(size))
                self.rfile.readline()  # CRLF after each chunk
            return b"".join(chunks)
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length)

    def _handle(self, code: int) -> None:
        _received["method"] = self.command
        _received["content_type"] = self.headers.get("Content-Type")
        _received["path"] = self.path
        _received["body"] = self._read_body()
        self.send_response(code)
        self.end_headers()

    def do_PUT(self) -> None:
        self._handle(204)

    def do_POST(self) -> None:
        self._handle(204)

    def log_message(self, *_a: Any) -> None:
        pass


class _RejectHandler(_StoreHandler):
    def do_PUT(self) -> None:
        self._read_body()  # drain body so httpx sees the response, not a reset
        self.send_response(400)
        self.end_headers()


def _serve(handler: type[BaseHTTPRequestHandler]) -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def store_stub() -> Iterator[str]:
    yield from _serve(_StoreHandler)


@pytest.fixture
def rejecting_store() -> Iterator[str]:
    yield from _serve(_RejectHandler)


@pytest.mark.unit
async def test_load_puts_to_store_with_content_type(store_stub: str) -> None:
    _received.clear()
    async with OxigraphHttpClient(store_stub) as client:
        await client.load(b"<a> <b> <c> .", content_type="text/turtle")
    assert _received["method"] == "PUT"
    assert _received["content_type"] == "text/turtle"
    assert _received["path"].endswith("/store?default")
    assert _received["body"] == b"<a> <b> <c> ."


@pytest.mark.unit
async def test_load_named_graph_uses_graph_param(store_stub: str) -> None:
    _received.clear()
    async with OxigraphHttpClient(store_stub) as client:
        await client.load(
            b"", content_type="text/turtle", graph_iri="urn:g", replace=False
        )
    assert _received["method"] == "POST"
    assert "graph=urn:g" in _received["path"]


@pytest.mark.unit
async def test_load_error_status_raises(rejecting_store: str) -> None:
    async with OxigraphHttpClient(rejecting_store) as client:
        with pytest.raises(StorageError, match="Store load failed"):
            await client.load(b"x", content_type="text/turtle")


@pytest.mark.unit
async def test_load_streams_a_binary_file_object(store_stub: str) -> None:
    # A binary file object (what owl_load streams a multi-hundred-MB OWL through) must
    # be sent as an async byte stream — a sync file handle is rejected by httpx's
    # AsyncClient. Chunk boundaries must reassemble to the exact bytes.
    payload = b"<a> <b> <c> ." * 100_000  # ~1.3 MB → spans multiple 1 MB chunks
    _received.clear()
    async with OxigraphHttpClient(store_stub) as client:
        await client.load(io.BytesIO(payload), content_type="application/rdf+xml")
    assert _received["method"] == "PUT"
    assert _received["body"] == payload
