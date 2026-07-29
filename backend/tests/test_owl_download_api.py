"""Behavioral tests for the download-only NCIt artifact-pair endpoint."""

from __future__ import annotations

import io
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import pytest
from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.dependencies import get_ncit_client
from backend.main import create_app

if TYPE_CHECKING:
    from collections.abc import Iterator

_ONTOLOGY_IRI = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl"


def _owl(version: str = "26.07d") -> bytes:
    return f"""<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:owl="http://www.w3.org/2002/07/owl#">
  <owl:Ontology rdf:about="{_ONTOLOGY_IRI}">
    <owl:versionInfo>{version}</owl:versionInfo>
  </owl:Ontology>
</rdf:RDF>
""".encode()


def _zip(member: str, payload: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(member, payload)
    return buffer.getvalue()


class _EvsHandler(BaseHTTPRequestHandler):
    bodies: ClassVar[dict[str, bytes]] = {
        "/Thesaurus.OWL.zip": _zip("Thesaurus.owl", _owl()),
        "/ThesaurusInf.OWL.zip": _zip("ThesaurusInferred.owl", _owl()),
    }

    def do_GET(self) -> None:
        body = self.bodies[self.path]
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
        response = client.post("/api/v1/refresh/ncit/download", json={})
    assert response.status_code == 401


@pytest.mark.api
def test_download_endpoint_certifies_pair_without_store_access(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, evs_server: str
) -> None:
    monkeypatch.setenv("NCIT_OWL_BASE_URL", evs_server)
    monkeypatch.setenv("NCIT_OWL_DIR", str(tmp_path))
    get_settings.cache_clear()

    class _StoreMustNotBeResolved:
        async def load(self, *_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("download endpoint reached the NCIt store")

    app = create_app()
    app.dependency_overrides[get_ncit_client] = _StoreMustNotBeResolved

    def _whole_file_read_forbidden(_path: Path) -> bytes:
        raise AssertionError(
            "download endpoint materialized an artifact with read_bytes"
        )

    monkeypatch.setattr(Path, "read_bytes", _whole_file_read_forbidden)
    with TestClient(app) as client:
        response = client.post("/api/v1/refresh/ncit/download", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["ontology_version"] == "26.07d"
    assert body["manifest_identity"]
    assert body["stated"]["file_path"] != body["inferred"]["file_path"]


@pytest.mark.api
def test_download_endpoint_rejects_legacy_load_option_before_store_access(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, evs_server: str
) -> None:
    monkeypatch.setenv("NCIT_OWL_BASE_URL", evs_server)
    monkeypatch.setenv("NCIT_OWL_DIR", str(tmp_path))
    get_settings.cache_clear()
    loaded = False

    class _RecordingStore:
        async def load(self, *_args: Any, **_kwargs: Any) -> None:
            nonlocal loaded
            loaded = True

    app = create_app()
    app.dependency_overrides[get_ncit_client] = _RecordingStore
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/refresh/ncit/download",
            json={"variant": "inferred", "load": True},
        )

    assert response.status_code == 422
    assert loaded is False
    assert not (tmp_path / "ncit-artifact-pair.json").exists()


@pytest.mark.api
def test_download_endpoint_reports_pair_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class _Failing(BaseHTTPRequestHandler):
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
        monkeypatch.setenv("NCIT_OWL_MAX_RETRIES", "0")
        get_settings.cache_clear()
        with TestClient(create_app()) as client:
            response = client.post("/api/v1/refresh/ncit/download", json={})
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status_code == 502
