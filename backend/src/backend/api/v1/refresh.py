"""Repository refresh / reload.

``POST /api/v1/refresh`` re-probes each repository and returns current version/counts
— a live status refresh. ``POST /api/v1/refresh/ncit/reload`` bulk-loads a server-side
RDF file into Oxigraph via the local Graph Store Protocol (no container/ECS restart).
``POST /api/v1/refresh/ncit/download`` fetches the NCIt OWL from NCI EVS (stated or
inferred variant) and optionally loads it — the built-in NCIt data-refresh mechanism.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from backend.config import get_settings
from backend.dependencies import CadsrRepo, NcitClient, NcitSearch, NcitStore
from backend.security import RequireApiKey
from ontolib.core.exceptions import StorageError
from ontolib.core.logging_config import get_logger
from ontolib.repositories.cadsr.download import download_cadsr_cdes
from ontolib.terminologies.ncit.owl_download import (
    OwlDownloadResult,
    download_ncit_owl,
)
from ontolib.terminologies.ncit.search_index import populate_from_store

logger = get_logger(__name__)

_OWL_CONTENT_TYPE = "application/rdf+xml"

router = APIRouter(prefix="/api/v1/refresh", tags=["refresh"])

_RDF_CONTENT_TYPES = {
    ".ttl": "text/turtle",
    ".nt": "application/n-triples",
    ".nq": "application/n-quads",
    ".rdf": "application/rdf+xml",
    ".owl": "application/rdf+xml",
}


class RepoStatus(BaseModel):
    """Live status of one repository after a refresh probe."""

    name: str
    healthy: bool
    version: str | None = None
    item_count: int | None = None
    error: str | None = None


class RefreshReport(BaseModel):
    """Result of a repository refresh: per-repository status."""

    refreshed_at: str
    repositories: list[RepoStatus]


class ReloadRequest(BaseModel):
    """A server-side RDF file to bulk-load into the NCIt store."""

    source_path: str
    replace: bool = True


class ReloadResponse(BaseModel):
    """Triple counts before/after a reload."""

    triples_before: int
    triples_after: int


def _resolve_allowed(source_path: str) -> Path:
    """Resolve *source_path* and require it to live inside the reload allowlist dir."""
    allowed_root = Path(get_settings().reload_allowed_dir).resolve()
    resolved = Path(source_path).resolve()
    if not resolved.is_relative_to(allowed_root):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"source_path must resolve within the reload allowlist directory "
            f"({allowed_root}).",
        )
    return resolved


@router.post("", response_model=RefreshReport, dependencies=[RequireApiKey])
async def refresh(
    store: NcitStore, client: NcitClient, cadsr: CadsrRepo
) -> RefreshReport:
    """Re-probe NCIt and caDSR and return their current version/counts."""
    repos = [await _ncit_status(client), _cadsr_status(cadsr)]
    _ = store  # store is wired for symmetry / future cache rebuilds
    return RefreshReport(refreshed_at=datetime.now(UTC).isoformat(), repositories=repos)


async def _ncit_status(client: NcitClient) -> RepoStatus:
    try:
        count = await client.count()
        version = await client.version()
    except StorageError as exc:
        return RepoStatus(name="ncit", healthy=False, error=str(exc))
    return RepoStatus(name="ncit", healthy=True, version=version, item_count=count)


def _cadsr_status(cadsr: CadsrRepo) -> RepoStatus:
    try:
        count = cadsr.count()
    except sqlite3.OperationalError as exc:
        return RepoStatus(name="cadsr", healthy=False, error=str(exc))
    return RepoStatus(name="cadsr", healthy=True, item_count=count)


@router.post(
    "/ncit/reload", response_model=ReloadResponse, dependencies=[RequireApiKey]
)
async def reload_ncit(client: NcitClient, body: ReloadRequest) -> ReloadResponse:
    """Bulk-load a server-side RDF file into the NCIt Oxigraph store.

    The source file must resolve inside the configured reload allowlist directory —
    an arbitrary host path (``../../etc/passwd``) is rejected, so this endpoint cannot
    be used to ingest or exfiltrate files outside the managed data area.
    """
    path = _resolve_allowed(body.source_path)
    content_type = _RDF_CONTENT_TYPES.get(path.suffix.lower())
    if content_type is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unsupported RDF extension {path.suffix}; "
            f"expected one of {sorted(_RDF_CONTENT_TYPES)}",
        )
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"File not found: {path}")
    try:
        before = await client.count()
        await client.load(
            path.read_bytes(), content_type=content_type, replace=body.replace
        )
        after = await client.count()
    except StorageError as exc:
        # A 5xx from a real store fault would otherwise leave no server-side trace
        # (HTTPException responses are not logged by the error handler).
        logger.exception("NCIt reload failed for %s", path)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "NCIt store reload failed."
        ) from exc
    return ReloadResponse(triples_before=before, triples_after=after)


class OwlDownloadRequest(BaseModel):
    """Request to fetch the NCIt OWL from NCI EVS (and optionally load it)."""

    variant: Literal["stated", "inferred"] = "inferred"
    load: bool = False


class OwlDownloadReport(BaseModel):
    """Result of an OWL download, plus store triple counts if it was loaded."""

    download: OwlDownloadResult
    triples_before: int | None = None
    triples_after: int | None = None


@router.post(
    "/ncit/download", response_model=OwlDownloadReport, dependencies=[RequireApiKey]
)
async def download_ncit(
    client: NcitClient, body: OwlDownloadRequest
) -> OwlDownloadReport:
    """Download the NCIt OWL from NCI EVS; with ``load=True``, reload it into the store.

    Loads into the default graph (a full store refresh). The download lands in the
    configured managed dir; a failed download or load returns 502.
    """
    settings = get_settings()
    result = await download_ncit_owl(
        Path(settings.ncit_owl_dir),
        variant=body.variant,
        base_url=settings.ncit_owl_base_url,
        max_retries=settings.ncit_owl_max_retries,
    )
    if not result.success:
        logger.error("NCIt OWL download failed: %s", result.error)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, result.error or "OWL download failed."
        )
    if not body.load or result.file_path is None:
        return OwlDownloadReport(download=result)
    try:
        before = await client.count()
        await client.load(
            Path(result.file_path).read_bytes(),
            content_type=_OWL_CONTENT_TYPE,
            replace=True,
        )
        after = await client.count()
    except (StorageError, OSError) as exc:
        logger.exception("NCIt OWL load failed for %s", result.file_path)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "NCIt store load failed."
        ) from exc
    return OwlDownloadReport(
        download=result, triples_before=before, triples_after=after
    )


class CdeDownloadReport(BaseModel):
    """Result of a caDSR CDE archive download: the cached zip + its version markers."""

    file_path: str
    cached: bool  # reused via 304 revalidation or served offline
    offline: bool  # served from cache because the caDSR host was unreachable
    source_last_modified: str | None = None
    source_etag: str | None = None


@router.post(
    "/cadsr/download", response_model=CdeDownloadReport, dependencies=[RequireApiKey]
)
async def download_cadsr() -> CdeDownloadReport:
    """Download the caDSR CDE XML archive from the caDSR host (cached, offline-safe).

    Fetches the source zip into the managed dir; conditional revalidation reuses an
    unchanged release and an unreachable host falls back to the cached copy. Building
    the CDE database from the XML is a separate step (#7). A terminal failure (bad URL
    / 4xx), or an unreachable host with no cached copy, returns 502; a local storage
    fault (unwritable dir, disk full) returns 500.
    """
    settings = get_settings()
    try:
        outcome = await download_cadsr_cdes(
            Path(settings.cadsr_data_dir),
            base_url=settings.cadsr_download_url,
            max_retries=settings.cadsr_download_max_retries,
        )
    except StorageError as exc:
        # Upstream fault: bad URL / 4xx, or unreachable with no cache to fall back to.
        logger.exception("caDSR CDE download failed")
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "caDSR CDE download failed."
        ) from exc
    except OSError as exc:
        # Local fault (disk full, permission denied, read-only mount) — not the host's.
        logger.exception("caDSR CDE local storage failure")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "caDSR CDE storage error."
        ) from exc
    if outcome.status == "offline":
        # Degraded success: surface it at the API layer, not just deep in ontolib, so
        # monitors keying on backend logs see that the source was unreachable.
        logger.warning(
            "caDSR CDE served from offline cache (source unreachable): %s", outcome.path
        )
    return CdeDownloadReport(
        file_path=outcome.path,
        cached=outcome.status != "downloaded",
        offline=outcome.status == "offline",
        source_last_modified=outcome.manifest.last_modified,
        source_etag=outcome.manifest.etag,
    )


class SearchIndexReport(BaseModel):
    """Result of rebuilding the NCIt full-text search cache."""

    concepts_indexed: int


@router.post(
    "/ncit/search-index",
    response_model=SearchIndexReport,
    dependencies=[RequireApiKey],
)
async def rebuild_ncit_search_index(
    store: NcitStore, index: NcitSearch
) -> SearchIndexReport:
    """Rebuild the NCIt FTS cache from the live store (materialize label + synonyms).

    Run after an NCIt store (re)load: search then serves from the tsvector index
    instead of a live SPARQL scan. A store or DB failure returns 502.
    """
    try:
        count = await populate_from_store(store, index)
    except (StorageError, SQLAlchemyError) as exc:
        logger.exception("NCIt search-index rebuild failed")
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "NCIt search-index rebuild failed."
        ) from exc
    return SearchIndexReport(concepts_indexed=count)
