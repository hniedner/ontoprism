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
from typing import TYPE_CHECKING

import httpx
from pydantic import BaseModel

from ontolib.core.exceptions import StorageError
from ontolib.core.logging_config import get_logger

if TYPE_CHECKING:
    from pathlib import Path

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


def _extract_owl(zip_path: Path, output_dir: Path) -> Path:
    """Extract the Thesaurus ``.owl`` member from *zip_path* into *output_dir*."""
    with zipfile.ZipFile(zip_path) as zf:
        owl_members = [n for n in zf.namelist() if n.lower().endswith(".owl")]
        if not owl_members:
            raise StorageError(f"No .owl member in archive {zip_path.name}")
        member = owl_members[0]
        zf.extract(member, output_dir)
    extracted = output_dir / member
    final = output_dir / DEFAULT_OWL_FILENAME
    if extracted != final:
        final.unlink(missing_ok=True)
        shutil.move(str(extracted), final)
    return final


async def download_ncit_owl(
    output_dir: Path,
    *,
    variant: str = "inferred",
    base_url: str = DEFAULT_OWL_BASE_URL,
    max_retries: int = 3,
) -> OwlDownloadResult:
    """Download and extract the NCIt OWL *variant* into *output_dir*.

    Skips the fetch when a cached zip already matches the remote size. Retries the
    transfer with exponential backoff; a persistent failure is returned as
    ``success=False`` (not raised) so the caller/endpoint can report it.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    url = owl_download_url(variant, base_url)
    zip_path = output_dir / _VARIANT_ZIPS[variant]

    remote_size = await _remote_size(url)
    cache_hit = (
        remote_size is not None
        and zip_path.exists()
        and zip_path.stat().st_size == remote_size
    )
    if cache_hit:
        owl = _extract_owl(zip_path, output_dir)
        return OwlDownloadResult(
            success=True,
            variant=variant,
            file_path=str(owl),
            size_bytes=owl.stat().st_size,
            cached=True,
        )

    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        if attempt:
            await asyncio.sleep(min(_RETRY_BASE_DELAY * 2 ** (attempt - 1), 60.0))
        temp = zip_path.with_suffix(".zip.tmp")
        try:
            await _stream_to(url, temp)
            shutil.move(str(temp), zip_path)
            owl = _extract_owl(zip_path, output_dir)
            return OwlDownloadResult(
                success=True,
                variant=variant,
                file_path=str(owl),
                size_bytes=owl.stat().st_size,
                cached=False,
            )
        except (httpx.HTTPError, StorageError) as exc:
            last_error = exc
            temp.unlink(missing_ok=True)
            logger.warning("OWL download attempt %d failed: %s", attempt + 1, exc)

    return OwlDownloadResult(
        success=False,
        variant=variant,
        error=f"OWL download failed after {max_retries + 1} attempt(s): {last_error}",
    )
