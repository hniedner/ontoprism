"""Behavioral tests for the NCIt OWL downloader.

Exercises the real download + unzip + cache logic against a local ``http.server``
serving a small in-memory OWL zip — no network, no live EVS, no mocks.
"""

from __future__ import annotations

import io
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from ontolib.terminologies.ncit.owl_download import (
    download_ncit_owl,
    owl_download_url,
    probe_owl_version,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

_OWL_BYTES = b"<?xml version='1.0'?><rdf:RDF>tiny ncit owl</rdf:RDF>"


def _make_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("Thesaurus.owl", _OWL_BYTES)
    return buffer.getvalue()


_ZIP_BYTES = _make_zip()


class _ZipHandler(BaseHTTPRequestHandler):
    def _send(self, body: bytes | None) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(len(_ZIP_BYTES)))
        self.end_headers()
        if body is not None:
            self.wfile.write(body)

    def do_HEAD(self) -> None:
        self._send(None)

    def do_GET(self) -> None:
        self._send(_ZIP_BYTES)

    def log_message(self, *_args: Any) -> None:
        pass


@pytest.fixture
def zip_server() -> Iterator[str]:
    """A local server that serves the OWL zip for GET/HEAD."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ZipHandler)
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
def test_owl_download_url_selects_variant() -> None:
    base = "https://evs.example/ftp1/NCI_Thesaurus"
    assert owl_download_url("stated", base) == f"{base}/Thesaurus.OWL.zip"
    assert owl_download_url("inferred", base) == f"{base}/ThesaurusInf.OWL.zip"
    with pytest.raises(ValueError, match="Unknown OWL variant"):
        owl_download_url("nonsense", base)


@pytest.mark.unit
async def test_download_extracts_owl_from_zip(zip_server: str, tmp_path: Path) -> None:
    result = await download_ncit_owl(
        tmp_path, variant="stated", base_url=zip_server, max_retries=0
    )
    assert result.success is True
    assert result.file_path is not None
    owl = Path(result.file_path)
    assert owl.exists()
    assert owl.read_bytes() == _OWL_BYTES
    assert result.cached is False


@pytest.mark.unit
async def test_download_uses_cache_when_zip_matches_remote_size(
    zip_server: str, tmp_path: Path
) -> None:
    first = await download_ncit_owl(
        tmp_path, variant="stated", base_url=zip_server, max_retries=0
    )
    assert first.cached is False
    second = await download_ncit_owl(
        tmp_path, variant="stated", base_url=zip_server, max_retries=0
    )
    assert second.success is True
    assert (
        second.cached is True
    )  # the local zip matches the remote size — skip re-fetch


@pytest.mark.unit
async def test_probe_owl_version_reports_size(zip_server: str) -> None:
    info = await probe_owl_version(owl_download_url("stated", zip_server))
    assert info.url.endswith("Thesaurus.OWL.zip")
    assert info.size_bytes == len(_ZIP_BYTES)


@pytest.mark.unit
async def test_download_reports_error_after_retries(tmp_path: Path) -> None:
    # A server that always 500s: the downloader must surface a failure, not raise.
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
        result = await download_ncit_owl(
            tmp_path, variant="stated", base_url=f"http://{host}:{port}", max_retries=0
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert result.success is False
    assert result.error
