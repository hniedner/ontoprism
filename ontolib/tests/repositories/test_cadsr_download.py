"""Behavioral tests for the caDSR CDE archive downloader (cached, offline-tolerant)."""

from __future__ import annotations

import io
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any

import pytest

from ontolib.repositories.cadsr.archive import extract_cadsr_archive
from ontolib.repositories.cadsr.build import build_database
from ontolib.repositories.cadsr.download import (
    CADSR_ZIP_FILENAME,
    download_cadsr_cdes,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_XML = b"""<DataElementsList><DataElement>
<PUBLICID>1</PUBLICID><VERSION>1</VERSION>
</DataElement></DataElementsList>"""


def _zip_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("cde_xml_20260701120000_1.xml", _XML)
    return buffer.getvalue()


_ZIP = _zip_bytes()
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

    def log_message(self, format: str, *args: Any) -> None:
        del format, args


@pytest.fixture
def cadsr_server() -> Iterator[str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address[:2]
    try:
        yield f"http://{host}:{port}/releasedCDEsXML-OD.zip"
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.unit
async def test_download_fetches_zip_to_expected_filename(
    cadsr_server: str, tmp_path: Path
) -> None:
    outcome = await download_cadsr_cdes(tmp_path, base_url=cadsr_server, max_retries=0)
    assert outcome.status == "downloaded"
    dest = tmp_path / CADSR_ZIP_FILENAME
    assert dest.exists()
    assert dest.read_bytes() == _ZIP
    # The version manifest is written so we know which release is cached.
    assert outcome.manifest.etag == _ETAG
    assert outcome.manifest.last_modified == _LAST_MODIFIED


@pytest.mark.unit
async def test_download_revalidates_unchanged_release_via_304(
    cadsr_server: str, tmp_path: Path
) -> None:
    # First call caches + records the ETag; the second sends If-None-Match and the
    # server answers 304, so the cached zip is reused with no re-transfer.
    await download_cadsr_cdes(tmp_path, base_url=cadsr_server, max_retries=0)
    outcome = await download_cadsr_cdes(tmp_path, base_url=cadsr_server, max_retries=0)
    assert outcome.status == "not_modified"
    assert (tmp_path / CADSR_ZIP_FILENAME).read_bytes() == _ZIP
    with extract_cadsr_archive(
        outcome,
        expected_url=cadsr_server,
        workspace_parent=tmp_path / "workspaces",
    ) as extracted:
        candidate = build_database(extracted, tmp_path / "not-modified.db")
    assert candidate.cde_count == 1


@pytest.mark.unit
async def test_download_offline_falls_back_to_cache(tmp_path: Path) -> None:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address[:2]
    base = f"http://{host}:{port}/releasedCDEsXML-OD.zip"
    await download_cadsr_cdes(tmp_path, base_url=base, max_retries=0)  # populate
    srv.shutdown()
    srv.server_close()  # caDSR host now unreachable

    outcome = await download_cadsr_cdes(tmp_path, base_url=base, max_retries=0)
    assert outcome.status == "offline"
    assert (tmp_path / CADSR_ZIP_FILENAME).read_bytes() == _ZIP
    with extract_cadsr_archive(
        outcome,
        expected_url=base,
        workspace_parent=tmp_path / "workspaces",
    ) as extracted:
        candidate = build_database(extracted, tmp_path / "offline.db")
    assert candidate.cde_count == 1
