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

import shutil
import zipfile
from pathlib import Path

import httpx
from pydantic import BaseModel

from ontolib.core.download_cache import (
    DownloadOutcome,
    cached_download,
    manifest_path,
)
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

_CONNECT_TIMEOUT = 30.0  # probe_owl_version HEAD timeout


class OwlVersionInfo(BaseModel):
    """Remote OWL artifact metadata from a HEAD probe."""

    url: str
    size_bytes: int | None = None
    last_modified: str | None = None


class OwlDownloadResult(BaseModel):
    """Outcome of an OWL download: the extracted file, or an error.

    ``source_last_modified`` / ``source_etag`` echo the cached source's version markers
    (from the download manifest) so a caller can see *which version* is on disk.
    """

    success: bool
    variant: str
    file_path: str | None = None
    size_bytes: int | None = None
    cached: bool = False
    source_last_modified: str | None = None
    source_etag: str | None = None
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


# A valid archive that simply lacks a .owl member — re-downloading the same URL
# would return the same archive, so this is terminal, not retryable.
class OwlContentError(StorageError):
    """The downloaded archive has no usable ``.owl`` member."""


def _extract_owl(zip_path: Path, output_dir: Path) -> Path:
    """Extract the Thesaurus ``.owl`` member from *zip_path* into *output_dir*.

    Raises:
        OwlContentError: the archive is valid but contains no ``.owl`` member.
        zipfile.BadZipFile: the archive is corrupt/truncated (retryable upstream).
        OSError: a filesystem error moving the extracted file.
    """
    try:
        with zipfile.ZipFile(zip_path) as zf:
            owl_members = [n for n in zf.namelist() if n.lower().endswith(".owl")]
            if not owl_members:
                raise OwlContentError(f"No .owl member in archive {zip_path.name}")
            # extract() returns the sanitized path it actually wrote to (defends
            # against zip-slip / absolute member names — never trust the raw entry).
            extracted = Path(zf.extract(owl_members[0], output_dir))
    except (RuntimeError, NotImplementedError) as exc:
        # Encrypted or unsupported-compression archive: structurally unusable, so
        # terminal (re-downloading the same URL won't help) — not a corrupt-bytes retry.
        raise OwlContentError(f"Unusable archive {zip_path.name}: {exc}") from exc
    final = output_dir / DEFAULT_OWL_FILENAME
    if extracted != final:
        final.unlink(missing_ok=True)
        shutil.move(str(extracted), str(final))
    return final


def _make_result(
    variant: str, owl: Path, outcome: DownloadOutcome
) -> OwlDownloadResult:
    return OwlDownloadResult(
        success=True,
        variant=variant,
        file_path=str(owl),
        size_bytes=owl.stat().st_size,
        cached=outcome.status != "downloaded",  # revalidated (304) or offline
        source_last_modified=outcome.manifest.last_modified,
        source_etag=outcome.manifest.etag,
    )


def _drop_cache(zip_path: Path) -> None:
    """Delete a bad archive and its manifest so the next call re-downloads."""
    zip_path.unlink(missing_ok=True)
    manifest_path(zip_path).unlink(missing_ok=True)


async def download_ncit_owl(
    output_dir: Path,
    *,
    variant: str = "inferred",
    base_url: str = DEFAULT_OWL_BASE_URL,
    max_retries: int = 3,
) -> OwlDownloadResult:
    """Download and extract the NCIt OWL *variant* into *output_dir*.

    Uses the metadata-aware cache (:func:`ontolib.core.download_cache.cached_download`):
    an unchanged remote answers 304 and the cached zip is reused; an unreachable remote
    falls back to the cached zip. Any failure is returned as ``success=False`` (never
    raised) so the caller/endpoint can report it cleanly. ``cached`` is True when the
    result came from the cache (revalidated or offline) rather than a fresh download.
    """
    try:
        url = owl_download_url(variant, base_url)
    except ValueError as exc:
        return OwlDownloadResult(success=False, variant=variant, error=str(exc))
    zip_path = output_dir / _VARIANT_ZIPS[variant]

    try:
        outcome = await cached_download(url, zip_path, max_retries=max_retries)
    except (StorageError, OSError) as exc:
        logger.error("NCIt OWL download failed: %s", exc)
        return OwlDownloadResult(success=False, variant=variant, error=str(exc))

    try:
        owl = _extract_owl(zip_path, output_dir)
    except (OwlContentError, zipfile.BadZipFile, OSError) as exc:
        _drop_cache(zip_path)  # never leave a bad archive cached
        logger.error("NCIt OWL archive unusable: %s", exc)
        return OwlDownloadResult(success=False, variant=variant, error=str(exc))

    return _make_result(variant, owl, outcome)
