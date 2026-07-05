"""Download the NCIt OWL ontology from NCI EVS as part of the refresh mechanism.

NCI Enterprise Vocabulary Services publishes the Thesaurus as zipped OWL/RDF at
``https://evs.nci.nih.gov/ftp1/NCI_Thesaurus/`` under a CC BY 4.0 licence. Two variants
matter here:

- ``stated``   → ``Thesaurus.OWL.zip``     — the asserted axioms; what the decomposition
  engine needs (no inferred-closure bleed, see DECISIONS D4).
- ``inferred`` → ``ThesaurusInf.OWL.zip``  — the materialised closure; what the running
  store currently holds.

The downloader streams the zip to a temp file, extracts the ``.owl``, and skips the
fetch when a local copy already matches the remote ``Content-Length`` (size cache).
Loading the extracted file into Oxigraph is a separate step (the store client's
``load``); this module only fetches bytes to disk.
"""

from __future__ import annotations

import asyncio
import shutil
import zipfile
from pathlib import Path

import httpx
from pydantic import BaseModel

from ontolib.core.exceptions import StorageError
from ontolib.core.logging_config import get_logger

logger = get_logger(__name__)

DEFAULT_OWL_BASE_URL = "https://evs.nci.nih.gov/ftp1/NCI_Thesaurus"
DEFAULT_OWL_FILENAME = "Thesaurus.owl"

# variant -> EVS zip filename
_VARIANT_ZIPS = {
    "stated": "Thesaurus.OWL.zip",
    "inferred": "ThesaurusInf.OWL.zip",
}

_DOWNLOAD_TIMEOUT = 1800.0  # 30 min — the OWL zip is hundreds of MB
_READ_TIMEOUT = 60.0
_CONNECT_TIMEOUT = 30.0
_CHUNK = 65536
_RETRY_BASE_DELAY = 5.0


class OwlVersionInfo(BaseModel):
    """Remote OWL artifact metadata from a HEAD probe."""

    url: str
    size_bytes: int | None = None
    last_modified: str | None = None


class OwlDownloadResult(BaseModel):
    """Outcome of an OWL download: the extracted file, or an error."""

    success: bool
    variant: str
    file_path: str | None = None
    size_bytes: int | None = None
    cached: bool = False
    error: str | None = None


def owl_download_url(variant: str, base_url: str = DEFAULT_OWL_BASE_URL) -> str:
    """Return the EVS download URL for the given OWL *variant*.

    Raises:
        ValueError: if *variant* is not ``stated`` or ``inferred``.
    """
    try:
        filename = _VARIANT_ZIPS[variant]
    except KeyError as exc:
        raise ValueError(
            f"Unknown OWL variant {variant!r}; expected one of {sorted(_VARIANT_ZIPS)}"
        ) from exc
    return f"{base_url.rstrip('/')}/{filename}"


async def probe_owl_version(url: str) -> OwlVersionInfo:
    """HEAD the OWL artifact and report its size / last-modified (best effort)."""
    async with httpx.AsyncClient() as client:
        response = await client.head(
            url, follow_redirects=True, timeout=_CONNECT_TIMEOUT
        )
        response.raise_for_status()
    raw_size = response.headers.get("content-length")
    return OwlVersionInfo(
        url=url,
        size_bytes=int(raw_size) if raw_size else None,
        last_modified=response.headers.get("last-modified"),
    )


async def _remote_size(url: str) -> int | None:
    """Return the remote Content-Length, or None if the HEAD fails (non-fatal)."""
    try:
        info = await probe_owl_version(url)
    except (httpx.HTTPError, OSError, ValueError) as exc:
        logger.warning("HEAD probe failed for %s: %s", url, exc)
        return None
    return info.size_bytes


async def _stream_to(url: str, dest: Path) -> int:
    """Stream *url* to *dest*; return bytes written. Raises on incomplete transfer."""
    timeout = httpx.Timeout(
        _DOWNLOAD_TIMEOUT, connect=_CONNECT_TIMEOUT, read=_READ_TIMEOUT
    )
    written = 0
    async with (
        httpx.AsyncClient(timeout=timeout) as client,
        client.stream("GET", url, follow_redirects=True) as response,
    ):
        response.raise_for_status()
        total = int(response.headers.get("content-length", 0))
        with dest.open("wb") as fh:
            async for chunk in response.aiter_bytes(_CHUNK):
                fh.write(chunk)
                written += len(chunk)
    if total and written < total:
        raise httpx.TransportError(f"Incomplete download: {written}/{total} bytes")
    return written


# A valid archive that simply lacks a .owl member — re-downloading the same URL
# would return the same archive, so this is terminal, not retryable.
class OwlContentError(StorageError):
    """The downloaded archive has no usable ``.owl`` member."""


# A failed transfer/extract worth another attempt (a truncated body decompresses to a
# BadZipFile, a socket drops, a move hits a transient FS error).
_RETRYABLE_DOWNLOAD = (httpx.HTTPError, zipfile.BadZipFile, OSError)


def _extract_owl(zip_path: Path, output_dir: Path) -> Path:
    """Extract the Thesaurus ``.owl`` member from *zip_path* into *output_dir*.

    Raises:
        OwlContentError: the archive is valid but contains no ``.owl`` member.
        zipfile.BadZipFile: the archive is corrupt/truncated (retryable upstream).
        OSError: a filesystem error moving the extracted file.
    """
    with zipfile.ZipFile(zip_path) as zf:
        owl_members = [n for n in zf.namelist() if n.lower().endswith(".owl")]
        if not owl_members:
            raise OwlContentError(f"No .owl member in archive {zip_path.name}")
        # extract() returns the sanitized path it actually wrote to (defends against
        # zip-slip / absolute member names — never trust the raw namelist entry).
        extracted = Path(zf.extract(owl_members[0], output_dir))
    final = output_dir / DEFAULT_OWL_FILENAME
    if extracted != final:
        final.unlink(missing_ok=True)
        shutil.move(str(extracted), str(final))
    return final


def _make_result(variant: str, owl: Path, *, cached: bool) -> OwlDownloadResult:
    return OwlDownloadResult(
        success=True,
        variant=variant,
        file_path=str(owl),
        size_bytes=owl.stat().st_size,
        cached=cached,
    )


def _try_cached(
    zip_path: Path, output_dir: Path, variant: str, remote_size: int | None
) -> OwlDownloadResult | None:
    """Return a cache-hit result if a valid local zip matches *remote_size*, else None.

    A right-sized but corrupt cached zip is dropped and treated as a miss so the caller
    re-downloads (self-heal) rather than raising forever.
    """
    if (
        remote_size is None
        or not zip_path.exists()
        or zip_path.stat().st_size != remote_size
    ):
        return None
    try:
        owl = _extract_owl(zip_path, output_dir)
    except (StorageError, *_RETRYABLE_DOWNLOAD) as exc:
        logger.warning("Cached OWL zip unusable (%s); re-downloading", exc)
        zip_path.unlink(missing_ok=True)
        return None
    return _make_result(variant, owl, cached=True)


async def _fetch_and_extract(
    url: str, temp: Path, zip_path: Path, output_dir: Path, variant: str
) -> OwlDownloadResult:
    """One download attempt: stream → move → extract. Raises on any failure."""
    await _stream_to(url, temp)
    shutil.move(str(temp), str(zip_path))
    owl = _extract_owl(zip_path, output_dir)
    return _make_result(variant, owl, cached=False)


async def download_ncit_owl(
    output_dir: Path,
    *,
    variant: str = "inferred",
    base_url: str = DEFAULT_OWL_BASE_URL,
    max_retries: int = 3,
) -> OwlDownloadResult:
    """Download and extract the NCIt OWL *variant* into *output_dir*.

    Skips the fetch when a cached zip already matches the remote size. Retries a
    transient transfer/extract failure with exponential backoff; a persistent or
    terminal failure is returned as ``success=False`` (never raised) so the
    caller/endpoint can report it cleanly.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    url = owl_download_url(variant, base_url)
    zip_path = output_dir / _VARIANT_ZIPS[variant]

    cached = _try_cached(zip_path, output_dir, variant, await _remote_size(url))
    if cached is not None:
        return cached

    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        if attempt:
            await asyncio.sleep(min(_RETRY_BASE_DELAY * 2 ** (attempt - 1), 60.0))
        temp = zip_path.with_suffix(".zip.tmp")
        try:
            return await _fetch_and_extract(url, temp, zip_path, output_dir, variant)
        except OwlContentError as exc:
            temp.unlink(missing_ok=True)
            logger.error("NCIt OWL archive unusable: %s", exc)
            return OwlDownloadResult(success=False, variant=variant, error=str(exc))
        except _RETRYABLE_DOWNLOAD as exc:
            last_error = exc
            temp.unlink(missing_ok=True)
            zip_path.unlink(missing_ok=True)  # drop a corrupt archive; never cache it
            logger.warning("OWL download attempt %d failed: %s", attempt + 1, exc)

    return OwlDownloadResult(
        success=False,
        variant=variant,
        error=f"OWL download failed after {max_retries + 1} attempt(s): {last_error}",
    )
