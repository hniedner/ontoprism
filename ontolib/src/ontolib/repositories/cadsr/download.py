"""Download the caDSR CDE XML archive from the NCI caDSR download host.

Fetches the released CDE XML dump (a zip, served over HTTPS from the ``/ftp/`` path)
through the metadata-aware cache (:func:`ontolib.core.download_cache.cached_download`) —
conditional revalidation reuses an unchanged release (304) and an unreachable host
serves the cached copy. Building the SQLite CDE repository from the XML is a separate
step (the standalone caDSR build, issue #7 — "Reproducible standalone data build");
this module only fetches the source zip.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ontolib.core.download_cache import DownloadOutcome, cached_download

if TYPE_CHECKING:
    from pathlib import Path

# NCI caDSR archive of released CDEs (XML). The ``/ftp/`` path segment is a legacy name;
# the artifact is served over HTTPS. Deployments override the URL via the
# ``cadsr_download_url`` setting (env ``CADSR_DOWNLOAD_URL``) to repoint at a mirror.
DEFAULT_CADSR_DOWNLOAD_URL = (
    "https://cadsr.nci.nih.gov/ftp/caDSR_Downloads/CDE/XML/releasedCDEsXML-OD.zip"
)
CADSR_ZIP_FILENAME = "releasedCDEsXML-OD.zip"


async def download_cadsr_cdes(
    output_dir: Path,
    *,
    base_url: str = DEFAULT_CADSR_DOWNLOAD_URL,
    max_retries: int = 3,
) -> DownloadOutcome:
    """Download the caDSR CDE XML zip into *output_dir* (cached, offline-tolerant).

    Returns a :class:`DownloadOutcome` (downloaded / not_modified / offline).
    Raises :class:`ontolib.core.exceptions.StorageError` on a terminal failure (bad URL
    / 4xx) or when the host is unreachable and there is no cached copy to fall back to.
    """
    dest = output_dir / CADSR_ZIP_FILENAME
    return await cached_download(base_url, dest, max_retries=max_retries)
