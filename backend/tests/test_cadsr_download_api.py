"""Behavioral tests for the caDSR CDE download endpoint (cached, offline-tolerant)."""

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.main import create_app

_ZIP = b"PK\x03\x04 fake caDSR CDE XML archive"
_ETAG = '"cadsr-v1"'
_LAST_MODIFIED = "Wed, 01 Jan 2025 00:00:00 GMT"


class _Handler(BaseHTTPRequestHandler):
    """Serves the fake archive, answering 304 to a matching conditional request."""

    def do_GET(self) -> None:
        if self.headers.get("If-None-Match") == _ETAG:
            self.send_response(304)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(_ZIP)))
        self.send_header("ETag", _ETAG)
        self.send_header("Last-Modified", _LAST_MODIFIED)
        self.end_headers()
        self.wfile.write(_ZIP)

    def log_message(self, *_a: Any) -> None:
        pass


def _start_server() -> tuple[ThreadingHTTPServer, str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address[:2]
    return srv, f"http://{host}:{port}/cdes.zip"


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.api
def test_cadsr_download_requires_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY", "s3cret")
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        assert client.post("/api/v1/refresh/cadsr/download").status_code == 401


@pytest.mark.api
def test_cadsr_download_fetches_and_reports_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    srv, url = _start_server()
    try:
        monkeypatch.setenv("CADSR_DOWNLOAD_URL", url)
        monkeypatch.setenv("CADSR_DATA_DIR", str(tmp_path))
        get_settings.cache_clear()
        with TestClient(create_app()) as client:
            resp = client.post("/api/v1/refresh/cadsr/download")
    finally:
        srv.shutdown()
        srv.server_close()
    assert resp.status_code == 200
    body = resp.json()
    assert body["cached"] is False
    assert body["offline"] is False
    assert body["source_etag"] == _ETAG
    assert body["source_last_modified"] == _LAST_MODIFIED
    dest = tmp_path / "releasedCDEsXML-OD.zip"
    assert body["file_path"] == str(dest)
    assert dest.read_bytes() == _ZIP


@pytest.mark.api
def test_cadsr_download_reuses_unchanged_release(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A second call revalidates via 304: reported cached=True but not offline (the
    # source was reachable and confirmed unchanged).
    srv, url = _start_server()
    try:
        monkeypatch.setenv("CADSR_DOWNLOAD_URL", url)
        monkeypatch.setenv("CADSR_DATA_DIR", str(tmp_path))
        get_settings.cache_clear()
        with TestClient(create_app()) as client:
            client.post("/api/v1/refresh/cadsr/download")
            resp = client.post("/api/v1/refresh/cadsr/download")
    finally:
        srv.shutdown()
        srv.server_close()
    assert resp.status_code == 200
    body = resp.json()
    assert body["cached"] is True
    assert body["offline"] is False


@pytest.mark.api
def test_cadsr_download_offline_serves_cache_with_200(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Core offline-tolerance guarantee: an unreachable source WITH a cached copy is a
    # degraded success (200, offline=True), not a 502.
    srv, url = _start_server()
    monkeypatch.setenv("CADSR_DOWNLOAD_URL", url)
    monkeypatch.setenv("CADSR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CADSR_DOWNLOAD_MAX_RETRIES", "0")
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        client.post("/api/v1/refresh/cadsr/download")  # populate the cache
        srv.shutdown()
        srv.server_close()  # caDSR host now unreachable
        resp = client.post("/api/v1/refresh/cadsr/download")
    assert resp.status_code == 200
    body = resp.json()
    assert body["offline"] is True
    assert body["cached"] is True
    assert (tmp_path / "releasedCDEsXML-OD.zip").read_bytes() == _ZIP


@pytest.mark.api
def test_cadsr_download_failure_is_502(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Unreachable host with no cache → a clean 502, not an unhandled 500.
    monkeypatch.setenv("CADSR_DOWNLOAD_URL", "http://127.0.0.1:9/cdes.zip")
    monkeypatch.setenv("CADSR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CADSR_DOWNLOAD_MAX_RETRIES", "0")
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        resp = client.post("/api/v1/refresh/cadsr/download")
    assert resp.status_code == 502


@pytest.mark.api
def test_cadsr_download_local_storage_fault_is_500(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A local storage fault (here: the cache dir's parent is a file, so mkdir raises
    # OSError) is the server's fault, not the host's → 500, not 502.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    monkeypatch.setenv("CADSR_DOWNLOAD_URL", "http://127.0.0.1:9/cdes.zip")
    monkeypatch.setenv("CADSR_DATA_DIR", str(blocker / "cadsr"))
    monkeypatch.setenv("CADSR_DOWNLOAD_MAX_RETRIES", "0")
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        resp = client.post("/api/v1/refresh/cadsr/download")
    assert resp.status_code == 500
