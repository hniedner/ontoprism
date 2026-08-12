"""Repository refresh and artifact download.

``POST /api/v1/refresh`` re-probes each repository and returns current version/counts
— a live status refresh. ``POST /api/v1/refresh/ncit/download`` fetches and certifies
the same-release stated/inferred NCIt pair without touching a running store.
"""

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import SQLAlchemyError

from backend.config import get_settings
from backend.dependencies import (
    NcitSearch,
    NcitStore,
    RepositoryMetadataReads,
    UberonSearch,
    UberonStore,
)
from backend.repository_metadata import (
    RepositoryMetadata,
    RepositoryMetadataError,
    RepositoryUnhealthy,
    observe_uberon_repository,
)
from backend.security import RequireApiKey
from ontolib.core.exceptions import StorageError
from ontolib.core.logging_config import get_logger
from ontolib.repositories.cadsr.download import download_cadsr_cdes
from ontolib.repositories.embeddings.generate import ncit_source_fingerprint
from ontolib.terminologies.ncit.owl_download import (
    OwlPairDownloadResult,
    download_ncit_owl_pair,
)
from ontolib.terminologies.ncit.search_index import populate_from_store
from ontolib.terminologies.uberon.search_index import (
    populate_from_store as populate_uberon_search,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/refresh", tags=["refresh"])


class RefreshReport(BaseModel):
    """Result of a repository refresh: certified identity or typed refusal."""

    refreshed_at: str
    repositories: list[RepositoryMetadata]


class ReloadRequest(BaseModel):
    """Legacy request retained only to return an explicit fail-closed response."""

    source_path: str
    replace: bool = True


@router.post("", response_model=RefreshReport, dependencies=[RequireApiKey])
async def refresh(
    metadata: RepositoryMetadataReads,
) -> RefreshReport:
    """Re-certify local proxies and return their exact active identities."""
    repositories: list[RepositoryMetadata] = [
        await metadata.ncit(),
        metadata.cadsr(),
        await metadata.uberon(),
    ]
    return RefreshReport(
        refreshed_at=datetime.now(UTC).isoformat(), repositories=repositories
    )


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
    """Result of rebuilding one terminology full-text search cache."""

    concepts_indexed: int


@router.post(
    "/ncit/search-index",
    response_model=SearchIndexReport,
    dependencies=[RequireApiKey],
)
async def rebuild_ncit_search_index(
    store: NcitStore,
    index: NcitSearch,
    metadata: RepositoryMetadataReads,
) -> SearchIndexReport:
    """Rebuild the NCIt FTS cache from the live store (materialize label + synonyms).

    Run after an NCIt store (re)load: search then serves from the tsvector index
    instead of a live SPARQL scan. An unhealthy NCIt repository returns 503; a store
    or DB failure during the rebuild returns 502.
    """
    repository = await metadata.ncit()
    if isinstance(repository, RepositoryUnhealthy):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            repository.model_dump(mode="json"),
        )
    try:
        source_before = await ncit_source_fingerprint(store)
        count = await populate_from_store(
            store,
            index,
            source_identity=repository.source_identity,
            source_hash=source_before[1],
        )
        source_after = await ncit_source_fingerprint(store)
        if source_after != source_before:
            raise StorageError("NCIt source changed during search-index rebuild")
    except (RepositoryMetadataError, StorageError, SQLAlchemyError) as exc:
        logger.exception("NCIt search-index rebuild failed")
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "NCIt search-index rebuild failed."
        ) from exc
    return SearchIndexReport(concepts_indexed=count)


@router.post(
    "/uberon/search-index",
    response_model=SearchIndexReport,
    dependencies=[RequireApiKey],
)
async def rebuild_uberon_search_index(
    store: UberonStore,
    index: UberonSearch,
    metadata: RepositoryMetadataReads,
) -> SearchIndexReport:
    """Rebuild Uberon/CL FTS from the exact certified immutable source."""
    repository = await metadata.uberon()
    if isinstance(repository, RepositoryUnhealthy):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            repository.model_dump(mode="json"),
        )
    try:

        async def validate_source() -> None:
            observation_after, counts_after = await observe_uberon_repository(
                get_settings().uberon_sparql_url
            )
            if (
                observation_after != repository.observation
                or counts_after != repository.class_counts
            ):
                raise StorageError(
                    "Uberon/CL source changed during search-index rebuild"
                )

        count = await populate_uberon_search(
            store,
            index,
            source_identity=repository.source_identity,
            source_hash=repository.source_sha256,
            validate_source=validate_source,
        )
    except (StorageError, SQLAlchemyError) as exc:
        logger.exception("Uberon/CL search-index rebuild failed")
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Uberon/CL search-index rebuild failed.",
        ) from exc
    return SearchIndexReport(concepts_indexed=count)
