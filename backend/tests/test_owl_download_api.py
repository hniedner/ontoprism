"""Behavioral tests for the NCIt OWL download endpoint.

Drives the real ASGI app and the real downloader against a local stub EVS server
serving a small OWL zip — no network, no live store (the load path uses a recording
fake client so the store is exercised through its real interface, not mocked away).
"""

import io
import threading
import zipfile
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.dependencies import get_ncit_client
from backend.main import create_app
from ontolib.core.exceptions import StorageError

_OWL_BYTES = b"<?xml version='1.0'?><rdf:RDF>tiny ncit owl</rdf:RDF>"


def _zip_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("Thesaurus.owl", _OWL_BYTES)
    return buffer.getvalue()


_ZIP = _zip_bytes()


class _EvsHandler(BaseHTTPRequestHandler):
    def _send(self, body: bytes | None) -> None:
        self.send_response(200)
        self.send_header("Content-Length", str(len(_ZIP)))
        self.end_headers()
        if body is not None:
            self.wfile.write(body)

    def do_HEAD(self) -> None:
        self._send(None)

    def do_GET(self) -> None:
        self._send(_ZIP)

    def log_message(self, *_args: Any) -> None:
        pass


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def evs_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _EvsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.api
def test_download_endpoint_requires_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY", "s3cret")
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        resp = client.post("/api/v1/refresh/ncit/download", json={"variant": "stated"})
    assert resp.status_code == 401


@pytest.mark.api
def test_download_endpoint_fetches_owl(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, evs_server: str
) -> None:
    monkeypatch.setenv("NCIT_OWL_BASE_URL", evs_server)
    monkeypatch.setenv("NCIT_OWL_DIR", str(tmp_path))
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        resp = client.post(
            "/api/v1/refresh/ncit/download",
            json={"variant": "stated", "load": False},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["download"]["success"] is True
    assert body["download"]["variant"] == "stated"
    assert body["download"]["file_path"]
    assert body["triples_before"] is None  # not loaded


@pytest.mark.api
def test_download_endpoint_loads_into_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, evs_server: str
) -> None:
    monkeypatch.setenv("NCIT_OWL_BASE_URL", evs_server)
    monkeypatch.setenv("NCIT_OWL_DIR", str(tmp_path))
    get_settings.cache_clear()

    class _RecordingClient:
        def __init__(self) -> None:
            self.loaded: bytes | None = None
            self._counts = iter([5, 9])

        async def count(self) -> int:
            return next(self._counts)

        async def load(self, data: bytes, **_kwargs: Any) -> None:
            self.loaded = data

    recording = _RecordingClient()
    app = create_app()
    app.dependency_overrides[get_ncit_client] = lambda: recording
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/refresh/ncit/download",
            json={"variant": "inferred", "load": True},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["triples_before"] == 5
    assert body["triples_after"] == 9
    assert recording.loaded == _OWL_BYTES  # the extracted OWL was loaded into the store


@pytest.mark.api
def test_download_endpoint_reports_load_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, evs_server: str
) -> None:
    # Download succeeds but the store rejects the OWL: the endpoint must return a
    # clean 502, not an unhandled 500.
    monkeypatch.setenv("NCIT_OWL_BASE_URL", evs_server)
    monkeypatch.setenv("NCIT_OWL_DIR", str(tmp_path))
    get_settings.cache_clear()

    class _RejectingClient:
        async def count(self) -> int:
            return 0

        async def load(self, *_a: Any, **_k: Any) -> None:
            raise StorageError("bad RDF")

    app = create_app()
    app.dependency_overrides[get_ncit_client] = _RejectingClient
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/refresh/ncit/download",
            json={"variant": "inferred", "load": True},
        )
    assert resp.status_code == 502


@pytest.mark.api
def test_download_endpoint_reports_download_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    class _Failing(BaseHTTPRequestHandler):
        def do_HEAD(self) -> None:
            self.send_response(500)
            self.end_headers()

        def do_GET(self) -> None:
            self.send_response(500)
            self.end_headers()

        def log_message(self, *_args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Failing)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        monkeypatch.setenv("NCIT_OWL_BASE_URL", f"http://{host}:{port}")
        monkeypatch.setenv("NCIT_OWL_DIR", str(tmp_path))
        monkeypatch.setenv("NCIT_OWL_MAX_RETRIES", "0")  # fail fast, no backoff sleeps
        get_settings.cache_clear()
        with TestClient(create_app()) as client:
            resp = client.post(
                "/api/v1/refresh/ncit/download",
                json={"variant": "stated", "load": False},
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert resp.status_code == 502
