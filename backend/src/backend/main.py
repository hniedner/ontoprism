"""FastAPI application entrypoint.

Owns process-wide QLever clients and repository read models; the frontend talks
only to this backend.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from backend import __version__
from backend.api.v1 import (
    cadsr,
    clinicaltrials,
    decomposition,
    mappings,
    ncit,
    pubmed,
    refresh,
    uberon,
)
from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from backend.decomposition_reader import DecompositionReader
from backend.dependencies import RepositoryMetadataReads
from backend.middleware import (
    RateLimitMiddleware,
    RequestContextMiddleware,
    install_error_handlers,
)
from backend.repository_metadata import RepositoryMetadataService, RepositoryUnhealthy
from ontolib.core.exceptions import StorageError
from ontolib.core.logging_config import get_logger
from ontolib.decomposition.provenance import ProvenanceStore
from ontolib.repositories.cadsr.repository import CdeRepository
from ontolib.repositories.clinicaltrials.client import ClinicalTrialsClient
from ontolib.repositories.embeddings.store import EmbeddingStore
from ontolib.repositories.pubmed.client import PubMedClient
from ontolib.repositories.xref.store import XrefStore
from ontolib.terminologies.ncit.client import ncit_sparql_client
from ontolib.terminologies.ncit.graph_store import NcitGraphStore
from ontolib.terminologies.ncit.search_index import NcitSearchIndex
from ontolib.terminologies.sparql_http_client import SparqlHttpClient
from ontolib.terminologies.uberon.graph_store import UberonGraphStore
from ontolib.terminologies.uberon.search_index import UberonSearchIndex

logger = get_logger(__name__)


async def check_ncit_version(client: SparqlHttpClient, expected: str) -> None:
    """Warn (don't fail) at startup if the store version differs from the pin.

    Roles are version-pinned (DECISIONS D5); a silent build bump would break them, so
    surface a mismatch loudly. Unreachable-at-startup is a warning, not a hard stop.
    """
    try:
        actual = await client.version()
    except StorageError as exc:
        logger.warning("NCIt version check skipped — store unreachable: %s", exc)
        return
    except Exception:
        # Background guard: an unexpected error must be logged where it happens and
        # never stored on the task (a stored exception would re-raise at shutdown and
        # skip client/engine cleanup). Warn, don't propagate.
        logger.exception("NCIt version check failed unexpectedly")
        return
    if actual != expected:
        logger.warning(
            "NCIt store version mismatch: expected %s, store reports %s "
            "(roles are version-pinned — verify before trusting results).",
            expected,
            actual,
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open terminology clients, repository stores, and publication stores."""
    settings = get_settings()
    if not settings.api_key:
        # Surface an intended-auth misconfiguration (blank/unset key) instead of
        # silently running the mutating endpoints wide open.
        logger.warning(
            "API_KEY is not set — refresh/reload endpoints run unauthenticated "
            "(open mode). Set api_key to require X-API-Key."
        )
    client = ncit_sparql_client(settings.ncit_sparql_url)
    uberon_client = SparqlHttpClient.for_qlever(settings.uberon_sparql_url)
    engine = make_engine(settings.database_url)
    app.state.ncit_client = client
    app.state.ncit_store = NcitGraphStore(client)
    app.state.uberon_client = uberon_client
    app.state.uberon_store = UberonGraphStore(uberon_client)
    app.state.decomposition_reader = DecompositionReader(client)
    app.state.cadsr_repo = CdeRepository(settings.cadsr_db_path)
    app.state.repository_metadata = RepositoryMetadataService(
        settings=settings,
        cadsr=app.state.cadsr_repo,
    )
    app.state.embedding_store = EmbeddingStore(make_sessionmaker(engine))
    app.state.ncit_search_index = NcitSearchIndex(make_sessionmaker(engine))
    app.state.uberon_search_index = UberonSearchIndex(make_sessionmaker(engine))
    app.state.provenance_store = ProvenanceStore(make_sessionmaker(engine))
    app.state.xref_store = XrefStore(make_sessionmaker(engine))
    app.state.clinicaltrials_client = ClinicalTrialsClient(
        settings.clinicaltrials_api_url
    )
    app.state.pubmed_client = PubMedClient(
        settings.pubmed_api_url,
        api_key=settings.pubmed_api_key,
        requests_per_second=settings.pubmed_requests_per_second,
    )
    # Fire the version check in the background so startup neither blocks on nor is
    # coupled to store reachability (a down store must not slow app boot / tests).
    version_check = asyncio.create_task(
        check_ncit_version(client, settings.ncit_expected_version)
    )
    try:
        yield
    finally:
        version_check.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await version_check
        await client.aclose()
        await uberon_client.aclose()
        await app.state.clinicaltrials_client.aclose()
        await app.state.pubmed_client.aclose()
        await dispose_engine(engine)


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    app = FastAPI(title="ontoprism", version=__version__, lifespan=lifespan)
    settings = get_settings()

    # Added inner→outer: RateLimit runs after RequestContext (so a 429 carries the
    # request id), and CORS wraps everything.
    app.add_middleware(RateLimitMiddleware, limit=settings.rate_limit_per_minute)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    install_error_handlers(app)

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        """Liveness — the process is up (no dependency checks)."""
        return {"status": "ok", "version": __version__}

    @app.get("/ready", tags=["meta"])
    async def ready(metadata: RepositoryMetadataReads) -> dict[str, object]:
        """Readiness — certify each local terminology proxy or refuse."""
        repositories = [await metadata.ncit(), await metadata.uberon(force=True)]
        unhealthy = next(
            (
                repository
                for repository in repositories
                if isinstance(repository, RepositoryUnhealthy)
            ),
            None,
        )
        if unhealthy is not None:
            logger.warning(
                "Readiness certification failed — %s: %s",
                unhealthy.reason,
                unhealthy.message,
            )
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                unhealthy.model_dump(mode="json"),
            )
        return {
            "ready": True,
            "repository": repositories[0].model_dump(mode="json"),
            "repositories": [
                repository.model_dump(mode="json") for repository in repositories
            ],
        }

    app.include_router(ncit.router)
    app.include_router(uberon.router)
    app.include_router(mappings.router)
    app.include_router(cadsr.router)
    app.include_router(refresh.router)
    app.include_router(clinicaltrials.router)
    app.include_router(pubmed.router)
    app.include_router(decomposition.router)
    return app


app = create_app()
