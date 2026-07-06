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
    """Mutable server state so a test can change the served content mid-run."""

    body = b"ontology source v1"
    etag = _ETAG
    last_modified = _LAST_MODIFIED
    get_requests: list[dict[str, str]] = []  # noqa: RUF012


def _handler(state: type[_State]) -> type[BaseHTTPRequestHandler]:
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            state.get_requests.append(dict(self.headers))
            if self.headers.get("If-None-Match") == state.etag:
                self.send_response(304)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(state.body)))
            self.send_header("ETag", state.etag)
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
