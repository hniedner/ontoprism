"""Behavioral tests for the metadata-aware download cache.

Exercises real conditional revalidation (ETag/Last-Modified → 304) and offline
fallback against a local ``http.server`` — no network, no mocks. The stub records
request headers so we can assert the conditional request was actually sent.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any

import pytest

from ontolib.core import download_cache
from ontolib.core.download_cache import (
    cached_download,
    manifest_path,
    read_manifest,
)
from ontolib.core.exceptions import StorageError

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_ETAG = '"v1-abc"'
_LAST_MODIFIED = "Wed, 01 Jul 2026 00:00:00 GMT"


class _State:
    """Mutable server state so a test can change what/how the server serves mid-run."""

    body = b"ontology source v1"
    etag: str | None = _ETAG
    last_modified: str | None = _LAST_MODIFIED
    status = 200  # forced GET status; set to 500/404 to simulate a server error
    advertised_len: int | None = (
        None  # override Content-Length (for truncated transfer)
    )
    get_requests: list[dict[str, str]] = []  # noqa: RUF012


def _not_modified(state: type[_State], headers: dict[str, str]) -> bool:
    if state.etag is not None:
        return headers.get("If-None-Match") == state.etag
    # ETag-less origin: any If-Modified-Since revalidates to 304 for this stub.
    return state.last_modified is not None and "If-Modified-Since" in headers


def _handler(state: type[_State]) -> type[BaseHTTPRequestHandler]:
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            state.get_requests.append(dict(self.headers))
            if state.status != 200:
                self.send_response(state.status)
                self.end_headers()
                return
            if _not_modified(state, dict(self.headers)):
                self.send_response(304)
                self.end_headers()
                return
            self.send_response(200)
            length = state.advertised_len
            self.send_header(
                "Content-Length", str(length if length is not None else len(state.body))
            )
            if state.etag is not None:
                self.send_header("ETag", state.etag)
            if state.last_modified is not None:
                self.send_header("Last-Modified", state.last_modified)
            self.end_headers()
            self.wfile.write(state.body)

        def log_message(self, *_a: Any) -> None:
            pass

    return _Handler


@pytest.fixture
def server() -> Iterator[tuple[str, type[_State]]]:
    state = type("S", (_State,), {"get_requests": []})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _handler(state))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address[:2]
    try:
        yield f"http://{host}:{port}/src.owl", state
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.unit
async def test_first_download_writes_file_and_metadata_manifest(
    server: tuple[str, type[_State]], tmp_path: Path
) -> None:
    url, _state = server
    dest = tmp_path / "src.owl"
    outcome = await cached_download(url, dest)
    assert outcome.status == "downloaded"
    assert dest.read_bytes() == b"ontology source v1"
    # The manifest records the source's version markers (the "which version" answer).
    manifest = read_manifest(dest)
    assert manifest is not None
    assert manifest.etag == _ETAG
    assert manifest.last_modified == _LAST_MODIFIED
    assert manifest.size_bytes == len(b"ontology source v1")
    assert manifest.downloaded_at


@pytest.mark.unit
async def test_unchanged_source_revalidates_to_304_and_reuses_cache(
    server: tuple[str, type[_State]], tmp_path: Path
) -> None:
    url, state = server
    dest = tmp_path / "src.owl"
    await cached_download(url, dest)  # populate cache
    outcome = await cached_download(url, dest)  # second call
    assert outcome.status == "not_modified"
    assert dest.read_bytes() == b"ontology source v1"
    # The second request carried the conditional header (proves revalidation happened).
    assert state.get_requests[-1].get("If-None-Match") == _ETAG


@pytest.mark.unit
async def test_changed_source_redownloads_and_updates_manifest(
    server: tuple[str, type[_State]], tmp_path: Path
) -> None:
    url, state = server
    dest = tmp_path / "src.owl"
    await cached_download(url, dest)
    state.body = b"ontology source v2 - bigger"
    state.etag = '"v2-def"'
    outcome = await cached_download(url, dest)
    assert outcome.status == "downloaded"
    assert dest.read_bytes() == b"ontology source v2 - bigger"
    manifest = read_manifest(dest)
    assert manifest is not None
    assert manifest.etag == '"v2-def"'


@pytest.mark.unit
async def test_offline_falls_back_to_cached_file(tmp_path: Path) -> None:
    # Populate the cache from a live server, then take the server down and confirm a
    # subsequent call serves the cached copy instead of failing.
    state = type("S", (_State,), {"get_requests": []})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _handler(state))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address[:2]
    url = f"http://{host}:{port}/src.owl"
    dest = tmp_path / "src.owl"
    await cached_download(url, dest)
    srv.shutdown()
    srv.server_close()  # remote now unreachable

    outcome = await cached_download(url, dest, max_retries=0)
    assert outcome.status == "offline"
    assert dest.read_bytes() == b"ontology source v1"


@pytest.mark.unit
async def test_offline_without_cache_raises(tmp_path: Path) -> None:
    # No cached file + unreachable remote → a real error, not a silent empty result.
    dest = tmp_path / "missing.owl"
    with pytest.raises(StorageError):
        await cached_download("http://127.0.0.1:9/never.owl", dest, max_retries=0)
    assert not dest.exists()
    assert not manifest_path(dest).exists()


def _start(**overrides: Any) -> tuple[ThreadingHTTPServer, type[_State], str]:
    state = type("S", (_State,), {"get_requests": [], **overrides})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _handler(state))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address[:2]
    return srv, state, f"http://{host}:{port}/src.owl"


@pytest.mark.unit
async def test_server_error_falls_back_to_cache(
    server: tuple[str, type[_State]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A reachable-but-erroring origin (persistent 5xx) with a cache serves it offline.
    monkeypatch.setattr(download_cache, "_RETRY_BASE_DELAY", 0.0)
    url, state = server
    dest = tmp_path / "src.owl"
    await cached_download(url, dest)  # populate cache
    state.status = 500
    outcome = await cached_download(url, dest, max_retries=1)
    assert outcome.status == "offline"
    assert dest.read_bytes() == b"ontology source v1"


@pytest.mark.unit
async def test_incomplete_transfer_preserves_existing_cache(
    server: tuple[str, type[_State]], tmp_path: Path
) -> None:
    # A truncated re-fetch must not clobber the good cache (.tmp + move design).
    url, state = server
    dest = tmp_path / "src.owl"
    await cached_download(url, dest)  # v1 cached
    state.etag = '"v2"'  # source "changed" so the conditional yields a 200...
    state.body = b"the new larger body"
    state.advertised_len = 9999  # ...but the server truncates it
    outcome = await cached_download(url, dest, max_retries=0)
    assert outcome.status == "offline"  # truncated fetch failed → served cache
    assert dest.read_bytes() == b"ontology source v1"  # old good copy intact
    assert not (tmp_path / "src.owl.tmp").exists()  # no orphan temp file


@pytest.mark.unit
async def test_client_error_4xx_raises_fast(
    server: tuple[str, type[_State]], tmp_path: Path
) -> None:
    # A 4xx is terminal: raise immediately, do NOT retry (won't fix on retry).
    url, state = server
    state.status = 404
    dest = tmp_path / "src.owl"
    with pytest.raises(StorageError):
        await cached_download(url, dest, max_retries=2)
    assert len(state.get_requests) == 1  # exactly one attempt


@pytest.mark.unit
async def test_corrupt_manifest_does_not_defeat_offline(tmp_path: Path) -> None:
    # A corrupt sidecar must not turn a usable on-disk cache into "no cache available".
    srv, _state, url = _start()
    dest = tmp_path / "src.owl"
    try:
        await cached_download(url, dest)
        manifest_path(dest).write_text("{ not valid json")  # corrupt the sidecar
        assert read_manifest(dest) is None  # exists-but-unreadable → None (logged)
    finally:
        srv.shutdown()
        srv.server_close()  # remote now unreachable

    outcome = await cached_download(url, dest, max_retries=0)
    assert outcome.status == "offline"  # keyed off the file, not the manifest
    assert dest.read_bytes() == b"ontology source v1"


@pytest.mark.unit
async def test_revalidates_via_if_modified_since_without_etag(tmp_path: Path) -> None:
    # A date-only origin (no ETag) revalidates via If-Modified-Since → 304 → reuse.
    srv, state, url = _start(etag=None)
    dest = tmp_path / "src.owl"
    try:
        await cached_download(url, dest)  # 200; manifest gets last_modified, no etag
        outcome = await cached_download(url, dest)
        assert outcome.status == "not_modified"
        assert "If-Modified-Since" in state.get_requests[-1]
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.unit
async def test_revalidates_without_last_modified_uses_etag_only(tmp_path: Path) -> None:
    # An origin with only ETag (no Last-Modified) must not include If-Modified-Since.
    srv, state, url = _start(last_modified=None)
    dest = tmp_path / "src.owl"
    try:
        await cached_download(url, dest)  # 200; manifest gets etag, no last_modified
        outcome = await cached_download(url, dest)
        assert outcome.status == "not_modified"
        last = state.get_requests[-1]
        assert "If-None-Match" in last
        assert "If-Modified-Since" not in last
    finally:
        srv.shutdown()
        srv.server_close()
