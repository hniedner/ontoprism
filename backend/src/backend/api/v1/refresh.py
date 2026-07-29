"""Repository refresh and artifact download.

``POST /api/v1/refresh`` re-probes each repository and returns current version/counts
— a live status refresh. ``POST /api/v1/refresh/ncit/download`` fetches and certifies
the same-release stated/inferred NCIt pair without touching a running store.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import SQLAlchemyError

from backend.config import get_settings
from backend.dependencies import (
    CadsrRepo,
    NcitSearch,
    NcitStatus,
    NcitStore,
)
from backend.security import RequireApiKey
from ontolib.core.exceptions import StorageError
from ontolib.core.logging_config import get_logger
from ontolib.repositories.cadsr.download import download_cadsr_cdes
from ontolib.terminologies.ncit.owl_download import (
    OwlPairDownloadResult,
    download_ncit_owl_pair,
)
from ontolib.terminologies.ncit.search_index import populate_from_store

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/refresh", tags=["refresh"])


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
    """Legacy request retained only to return an explicit fail-closed response."""

    source_path: str
    replace: bool = True


@router.post("", response_model=RefreshReport, dependencies=[RequireApiKey])
async def refresh(
    store: NcitStore, client: NcitStatus, cadsr: CadsrRepo
) -> RefreshReport:
    """Re-probe NCIt and caDSR and return their current version/counts."""
    repos = [await _ncit_status(client), _cadsr_status(cadsr)]
    _ = store  # store is wired for symmetry / future cache rebuilds
    return RefreshReport(refreshed_at=datetime.now(UTC).isoformat(), repositories=repos)


async def _ncit_status(client: NcitStatus) -> RepoStatus:
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


@router.post("/ncit/reload", dependencies=[RequireApiKey])
async def reload_ncit(_body: ReloadRequest) -> None:
    """Reject the removed generic source-ontology HTTP loader."""
    raise HTTPException(
        status.HTTP_410_GONE,
        "NCIt HTTP reload is disabled; build a validated sibling store offline.",
    )


class OwlDownloadRequest(BaseModel):
    """An intentionally empty download-only request."""

    model_config = ConfigDict(extra="forbid")


@router.post(
    "/ncit/download",
    response_model=OwlPairDownloadResult,
    dependencies=[RequireApiKey],
)
async def download_ncit(
    _body: OwlDownloadRequest,
) -> OwlPairDownloadResult:
    """Download and certify a same-release NCIt pair without store access."""
    settings = get_settings()
    result = await download_ncit_owl_pair(
        Path(settings.ncit_owl_dir),
        base_url=settings.ncit_owl_base_url,
        max_retries=settings.ncit_owl_max_retries,
    )
    if not result.success:
        logger.error("NCIt OWL pair download failed: %s", result.error)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            result.error or "NCIt artifact-pair download failed.",
        )
    return result


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
