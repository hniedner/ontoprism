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

from ontolib.core import download_cache as dl_cache
from ontolib.terminologies.ncit.owl_download import (
    download_ncit_owl,
    owl_download_url,
    probe_owl_version,
)


def _encrypted_zip(data: bytes) -> bytes:
    """Zip with encryption bit set so extract raises ``RuntimeError``."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Thesaurus.owl", data)
    raw = bytearray(buffer.getvalue())
    # General-purpose bit flag at offset 6 in local header, offset 8 in central dir.
    for marker in (b"PK\x03\x04", b"PK\x01\x02"):
        idx = raw.find(marker)
        if idx >= 0:
            raw[idx + 6 if marker == b"PK\x03\x04" else idx + 8] |= 0x01
    return bytes(raw)


if TYPE_CHECKING:
    from collections.abc import Iterator

_OWL_BYTES = b"<?xml version='1.0'?><rdf:RDF>tiny ncit owl</rdf:RDF>"


def _make_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("Thesaurus.owl", _OWL_BYTES)
    return buffer.getvalue()


_ZIP_BYTES = _make_zip()


_ZIP_ETAG = '"ncit-zip-v1"'
_ZIP_LAST_MODIFIED = "Wed, 01 Jul 2026 00:00:00 GMT"


class _ZipHandler(BaseHTTPRequestHandler):
    def _send(self, body: bytes | None) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(len(_ZIP_BYTES)))
        self.send_header("ETag", _ZIP_ETAG)
        self.send_header("Last-Modified", _ZIP_LAST_MODIFIED)
        self.end_headers()
        if body is not None:
            self.wfile.write(body)

    def do_HEAD(self) -> None:
        self._send(None)

    def do_GET(self) -> None:
        # Conditional revalidation: an unchanged zip answers 304 so the cache is reused.
        if self.headers.get("If-None-Match") == _ZIP_ETAG:
            self.send_response(304)
            self.end_headers()
            return
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
async def test_download_reuses_cache_when_source_unchanged(
    zip_server: str, tmp_path: Path
) -> None:
    first = await download_ncit_owl(
        tmp_path, variant="stated", base_url=zip_server, max_retries=0
    )
    assert first.cached is False
    # Second call revalidates via ETag → the server answers 304 → the cache is reused.
    second = await download_ncit_owl(
        tmp_path, variant="stated", base_url=zip_server, max_retries=0
    )
    assert second.success is True
    assert second.cached is True


@pytest.mark.unit
async def test_offline_serves_cached_owl_and_flags_it(tmp_path: Path) -> None:
    # Populate the cache, take the remote down, then confirm the reload succeeds from
    # cache AND flags offline=True so the operator can see it served a stale copy.
    server, base = _serve(_ZipHandler)
    first = await download_ncit_owl(
        tmp_path, variant="stated", base_url=base, max_retries=0
    )
    assert first.success is True
    assert first.offline is False
    server.shutdown()
    server.server_close()  # remote now unreachable

    result = await download_ncit_owl(
        tmp_path, variant="stated", base_url=base, max_retries=0
    )
    assert result.success is True
    assert result.offline is True
    assert result.cached is True
    assert result.file_path is not None
    assert Path(result.file_path).read_bytes() == _OWL_BYTES


@pytest.mark.unit
async def test_result_surfaces_source_version_metadata(
    zip_server: str, tmp_path: Path
) -> None:
    # The result echoes the source's version markers so a caller knows which version
    # is on disk (the user's "which version have we cached" question).
    result = await download_ncit_owl(
        tmp_path, variant="stated", base_url=zip_server, max_retries=0
    )
    assert result.source_etag == _ZIP_ETAG
    assert result.source_last_modified == _ZIP_LAST_MODIFIED


@pytest.mark.unit
async def test_probe_owl_version_reports_size(zip_server: str) -> None:
    info = await probe_owl_version(owl_download_url("stated", zip_server))
    assert info.url.endswith("Thesaurus.OWL.zip")
    assert info.size_bytes == len(_ZIP_BYTES)


def _zip_with(member: str, data: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(member, data)
    return buffer.getvalue()


def _serve(
    handler_cls: type[BaseHTTPRequestHandler],
) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}"


def _bytes_handler(
    body: bytes, *, advertised_len: int | None = None
) -> type[BaseHTTPRequestHandler]:
    length = advertised_len if advertised_len is not None else len(body)

    class _Handler(BaseHTTPRequestHandler):
        def _headers(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", str(length))
            self.end_headers()

        def do_HEAD(self) -> None:
            self._headers()

        def do_GET(self) -> None:
            self._headers()
            self.wfile.write(body)

        def log_message(self, *_a: Any) -> None:
            pass

    return _Handler


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

    server, base = _serve(_Failing)
    try:
        result = await download_ncit_owl(
            tmp_path, variant="stated", base_url=base, max_retries=0
        )
    finally:
        server.shutdown()
        server.server_close()
    assert result.success is False
    assert result.error


@pytest.mark.unit
async def test_download_reports_error_on_unwritable_dir(tmp_path: Path) -> None:
    # A misconfigured/unwritable download dir must be reported as success=False, not
    # escape as an unhandled error (mkdir failure honours the "never raise" contract).
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a dir")
    result = await download_ncit_owl(
        blocker / "sub", variant="stated", base_url="http://unused", max_retries=0
    )
    assert result.success is False
    assert result.error is not None


@pytest.mark.unit
async def test_download_reports_error_on_malformed_base_url(tmp_path: Path) -> None:
    # A misconfigured NCIT_OWL_BASE_URL (no scheme) must be reported as success=False,
    # not escape as an unhandled error (httpx.InvalidURL is not an HTTPError).
    result = await download_ncit_owl(
        tmp_path, variant="stated", base_url="not-a-valid-url", max_retries=2
    )
    assert result.success is False
    assert result.error is not None


@pytest.mark.unit
async def test_download_returns_error_on_no_owl_member(tmp_path: Path) -> None:
    # A valid archive lacking a .owl member is a terminal failure returned, not raised —
    # and it must NOT be retried (re-downloading the same URL yields the same archive).
    zip_bytes = _zip_with("readme.txt", b"nothing here")

    class _Counting(BaseHTTPRequestHandler):
        gets = 0

        def do_HEAD(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", str(len(zip_bytes)))
            self.end_headers()

        def do_GET(self) -> None:
            _Counting.gets += 1
            self.send_response(200)
            self.send_header("Content-Length", str(len(zip_bytes)))
            self.end_headers()
            self.wfile.write(zip_bytes)

        def log_message(self, *_a: Any) -> None:
            pass

    server, base = _serve(_Counting)
    try:
        result = await download_ncit_owl(
            tmp_path, variant="stated", base_url=base, max_retries=2
        )
    finally:
        server.shutdown()
        server.server_close()
    assert result.success is False
    assert result.error is not None
    assert "No .owl member" in result.error
    assert _Counting.gets == 1  # terminal — not retried despite max_retries=2


@pytest.mark.unit
async def test_download_returns_error_on_corrupt_zip(tmp_path: Path) -> None:
    # Bytes that are not a zip decompress to BadZipFile — surfaced as success=False,
    # not an escaping exception (the "return, don't raise" contract).
    server, base = _serve(_bytes_handler(b"this is not a zip file"))
    try:
        result = await download_ncit_owl(
            tmp_path, variant="stated", base_url=base, max_retries=0
        )
    finally:
        server.shutdown()
        server.server_close()
    assert result.success is False
    # A corrupt archive must not be left cached to poison the next call.
    assert not (tmp_path / "Thesaurus.OWL.zip").exists()


@pytest.mark.unit
async def test_download_renames_non_root_member(tmp_path: Path) -> None:
    # Real inferred archives carry ThesaurusInf.owl; it must be normalized to
    # Thesaurus.owl (exercises the rename/move branch).
    zip_bytes = _zip_with("ThesaurusInf.owl", _OWL_BYTES)
    server, base = _serve(_bytes_handler(zip_bytes))
    try:
        result = await download_ncit_owl(
            tmp_path, variant="inferred", base_url=base, max_retries=0
        )
    finally:
        server.shutdown()
        server.server_close()
    assert result.success is True
    assert result.file_path is not None
    owl = Path(result.file_path)
    assert owl.name == "Thesaurus.owl"
    assert owl.read_bytes() == _OWL_BYTES


@pytest.mark.unit
async def test_download_retries_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # First GET fails, second succeeds — the retry loop must recover.
    monkeypatch.setattr(dl_cache, "_RETRY_BASE_DELAY", 0.0)  # no real backoff sleep
    good_zip = _make_zip()

    class _FlipFlop(BaseHTTPRequestHandler):
        gets = 0

        def do_HEAD(self) -> None:
            self.send_response(500)  # 5xx on the first attempt (retryable)
            self.end_headers()

        def do_GET(self) -> None:
            _FlipFlop.gets += 1
            if _FlipFlop.gets == 1:
                self.send_response(500)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(good_zip)))
            self.end_headers()
            self.wfile.write(good_zip)

        def log_message(self, *_a: Any) -> None:
            pass

    server, base = _serve(_FlipFlop)
    try:
        result = await download_ncit_owl(
            tmp_path, variant="stated", base_url=base, max_retries=2
        )
    finally:
        server.shutdown()
        server.server_close()
    assert result.success is True
    assert _FlipFlop.gets == 2  # failed once, then succeeded


@pytest.mark.unit
async def test_download_detects_incomplete_transfer(tmp_path: Path) -> None:
    # Server advertises more bytes than it sends: the short read must fail the attempt.
    server, base = _serve(_bytes_handler(b"short", advertised_len=9999))
    try:
        result = await download_ncit_owl(
            tmp_path, variant="stated", base_url=base, max_retries=0
        )
    finally:
        server.shutdown()
        server.server_close()
    assert result.success is False


@pytest.mark.unit
async def test_corrupt_cached_zip_is_dropped_and_refetched(tmp_path: Path) -> None:
    # A corrupt cached zip with NO manifest: no conditional header is sent, so a plain
    # 200 re-downloads over it; extraction succeeds → self-heal, not a hard failure.
    good_zip = _make_zip()
    corrupt = b"x" * len(good_zip)
    (tmp_path / "Thesaurus.OWL.zip").write_bytes(corrupt)
    server, base = _serve(_bytes_handler(good_zip))
    try:
        result = await download_ncit_owl(
            tmp_path, variant="stated", base_url=base, max_retries=0
        )
    finally:
        server.shutdown()
        server.server_close()
    assert result.success is True
    assert (
        result.cached is False
    )  # the corrupt cache was rejected, a fresh copy fetched


@pytest.mark.unit
async def test_probe_owl_version_reports_last_modified(tmp_path: Path) -> None:
    class _WithDate(BaseHTTPRequestHandler):
        def do_HEAD(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", "10")
            self.send_header("Last-Modified", "Wed, 01 Jul 2026 00:00:00 GMT")
            self.end_headers()

        def log_message(self, *_a: Any) -> None:
            pass

    server, base = _serve(_WithDate)
    try:
        info = await probe_owl_version(owl_download_url("stated", base))
    finally:
        server.shutdown()
        server.server_close()
    assert info.last_modified == "Wed, 01 Jul 2026 00:00:00 GMT"


@pytest.mark.unit
async def test_download_reports_error_on_invalid_variant(tmp_path: Path) -> None:
    result = await download_ncit_owl(
        tmp_path, variant="nonsense", base_url="http://unused", max_retries=0
    )
    assert result.success is False
    assert result.error is not None


@pytest.mark.unit
async def test_download_reports_error_on_encrypted_zip(tmp_path: Path) -> None:
    zip_bytes = _encrypted_zip(_OWL_BYTES)
    server, base = _serve(_bytes_handler(zip_bytes))
    try:
        result = await download_ncit_owl(
            tmp_path, variant="stated", base_url=base, max_retries=0
        )
    finally:
        server.shutdown()
        server.server_close()
    assert result.success is False
    assert result.error is not None
    assert "Unusable archive" in result.error
    assert not (tmp_path / "Thesaurus.OWL.zip").exists()
