"""Metadata-aware download cache: conditional revalidation + offline fallback.

Each cached file gets a JSON sidecar manifest (``<name>.meta.json``) recording the
source's version markers — remote ``ETag`` / ``Last-Modified`` / size / fetch time — so
we know *which version* is on disk. On the next fetch:

- a **conditional** request (``If-None-Match`` / ``If-Modified-Since``) lets an
  unchanged source answer ``304 Not Modified`` and we reuse the cache with no transfer;
- if the remote is **unreachable**, we fall back to the cached file (with a warning)
  rather than failing — so a reload can still proceed offline.

This is the piece fairdata's size-only ``download_cache`` lacks; built here (TDD) and
shared by the NCIt and caDSR downloaders.
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime
from typing import TYPE_CHECKING, NoReturn

import httpx
from pydantic import BaseModel

from ontolib.core.exceptions import StorageError
from ontolib.core.logging_config import get_logger

if TYPE_CHECKING:
    from pathlib import Path

logger = get_logger(__name__)

_CHUNK = 65536
_CONNECT_TIMEOUT = 30.0
_READ_TIMEOUT = 60.0
_DOWNLOAD_TIMEOUT = 1800.0
_RETRY_BASE_DELAY = 5.0
_SERVER_ERROR = 500  # >= this is a (retryable) 5xx; below is a terminal 4xx

# Retryable → retry, then offline fallback. httpx.RequestError covers transport,
# timeout, redirect-loop and decoding; HTTPStatusError adds 5xx. Terminal errors (bad
# URL, 4xx) are converted to StorageError in _attempt before they reach the loop.
_RETRYABLE = (httpx.RequestError, httpx.HTTPStatusError)


class CacheManifest(BaseModel):
    """Provenance of a cached source file — the answer to 'which version is on disk'."""

    url: str
    downloaded_at: str
    size_bytes: int
    etag: str | None = None
    last_modified: str | None = None


class DownloadOutcome(BaseModel):
    """Result of a cached download and how it was satisfied."""

    path: str
    status: str  # "downloaded" | "not_modified" | "offline"
    manifest: CacheManifest


def manifest_path(dest: Path) -> Path:
    """Return the sidecar manifest path for *dest*."""
    return dest.with_name(dest.name + ".meta.json")


def read_manifest(dest: Path) -> CacheManifest | None:
    """Load *dest*'s manifest, or None if absent/corrupt."""
    path = manifest_path(dest)
    if not path.exists():
        return None
    try:
        return CacheManifest.model_validate_json(path.read_text())
    except (OSError, ValueError) as exc:
        # Exists but unreadable — surface it (a silent None would defeat both the
        # conditional request and, worse, the offline fallback if it keyed off it).
        logger.warning("Ignoring unreadable cache manifest %s: %s", path, exc)
        return None


def write_manifest(dest: Path, manifest: CacheManifest) -> None:
    """Persist *manifest* beside *dest*."""
    manifest_path(dest).write_text(manifest.model_dump_json(indent=2))


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _conditional_headers(manifest: CacheManifest | None) -> dict[str, str]:
    if manifest is None:
        return {}
    headers: dict[str, str] = {}
    if manifest.etag:
        headers["If-None-Match"] = manifest.etag
    if manifest.last_modified:
        headers["If-Modified-Since"] = manifest.last_modified
    return headers


def _require_manifest(dest: Path, url: str) -> CacheManifest:
    """The manifest for a 304 response — synthesized if it went missing (race)."""
    existing = read_manifest(dest)
    if existing is not None:
        return existing
    return CacheManifest(url=url, downloaded_at=_now(), size_bytes=dest.stat().st_size)


async def _stream_to_dest(response: httpx.Response, dest: Path) -> None:
    # Stream to a temp file and only move it into place once complete, so a truncated or
    # mid-stream-failed transfer never clobbers an existing good cache at *dest*.
    tmp = dest.with_name(dest.name + ".tmp")
    moved = False
    try:
        written = 0
        with tmp.open("wb") as fh:
            async for chunk in response.aiter_bytes(_CHUNK):
                fh.write(chunk)
                written += len(chunk)
        raw_total = response.headers.get("content-length", "")
        total = int(raw_total) if raw_total.isdigit() else 0
        if total and written < total:
            raise httpx.TransportError(f"Incomplete download: {written}/{total} bytes")
        shutil.move(str(tmp), str(dest))
        moved = True
    finally:
        if not moved:
            tmp.unlink(missing_ok=True)  # clean the orphan on any failure


def _raise_if_terminal(exc: httpx.HTTPError | httpx.InvalidURL, url: str) -> NoReturn:
    """Turn a terminal HTTP error into StorageError; re-raise a retryable one.

    Terminal (config/client errors, fail fast): a malformed URL or a 4xx. Retryable
    (bubbles to the loop): a 5xx server error.
    """
    if isinstance(exc, (httpx.UnsupportedProtocol, httpx.InvalidURL)):
        raise StorageError(f"Bad download URL {url!r}: {exc}") from exc
    if (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code < _SERVER_ERROR
    ):
        raise StorageError(f"HTTP {exc.response.status_code} for {url}") from exc
    raise exc  # 5xx or other — retryable, bubble to the loop


async def _attempt(url: str, dest: Path, headers: dict[str, str]) -> DownloadOutcome:
    """One fetch attempt. Retryable errors (network/5xx) bubble to the caller."""
    timeout = httpx.Timeout(
        _DOWNLOAD_TIMEOUT, connect=_CONNECT_TIMEOUT, read=_READ_TIMEOUT
    )
    try:
        async with (
            httpx.AsyncClient(timeout=timeout) as client,
            client.stream(
                "GET", url, headers=headers, follow_redirects=True
            ) as response,
        ):
            if response.status_code == httpx.codes.NOT_MODIFIED:
                return DownloadOutcome(
                    path=str(dest),
                    status="not_modified",
                    manifest=_require_manifest(dest, url),
                )
            response.raise_for_status()
            await _stream_to_dest(response, dest)
            manifest = CacheManifest(
                url=url,
                downloaded_at=_now(),
                size_bytes=dest.stat().st_size,
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
            )
            write_manifest(dest, manifest)
            return DownloadOutcome(
                path=str(dest), status="downloaded", manifest=manifest
            )
    except (httpx.UnsupportedProtocol, httpx.InvalidURL, httpx.HTTPStatusError) as exc:
        _raise_if_terminal(exc, url)  # NoReturn: raises terminal or re-raises retryable


async def cached_download(
    url: str, dest: Path, *, max_retries: int = 2
) -> DownloadOutcome:
    """Download *url* to *dest* with conditional revalidation and offline fallback.

    Returns a :class:`DownloadOutcome` whose ``status`` is ``downloaded`` (fresh copy),
    ``not_modified`` (remote unchanged, cache reused), or ``offline`` (unreachable,
    cache reused). Raises :class:`StorageError` on a terminal error (bad URL, 4xx),
    or when the remote is unreachable and there is no cached copy to fall back to.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    headers = _conditional_headers(read_manifest(dest))
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        if attempt:
            await asyncio.sleep(min(_RETRY_BASE_DELAY * 2 ** (attempt - 1), 60.0))
        try:
            return await _attempt(url, dest, headers)
        except _RETRYABLE as exc:
            last_error = exc  # transport/redirect/decoding or 5xx — retry, then offline
            logger.warning(
                "download attempt %d failed for %s: %s", attempt + 1, url, exc
            )

    if dest.exists():
        # Offline fallback keys off the file on disk, not the manifest — a corrupt
        # sidecar must not turn a usable cache into "no cache available". Synthesize a
        # bare manifest when the sidecar is missing/unreadable.
        manifest = _require_manifest(dest, url)
        logger.warning(
            "Remote unreachable (%s); serving cached %s (offline).", last_error, dest
        )
        return DownloadOutcome(path=str(dest), status="offline", manifest=manifest)
    raise StorageError(
        f"Download failed and no cache available for {url}: {last_error}"
    )
