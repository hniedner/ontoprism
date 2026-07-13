"""FastAPI application entrypoint.

Owns the process-wide Oxigraph SPARQL client (opened for the app lifespan) and the
NCIt repository read model; the frontend talks only to this backend.
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
    sparql,
)
from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from backend.dependencies import NcitClient
from backend.middleware import (
    RateLimitMiddleware,
    RequestContextMiddleware,
    install_error_handlers,
)
from ontolib.core.exceptions import StorageError
from ontolib.core.logging_config import get_logger
from ontolib.decomposition.provenance import ProvenanceStore
from ontolib.repositories.cadsr.repository import CdeRepository
from ontolib.repositories.clinicaltrials.client import ClinicalTrialsClient
from ontolib.repositories.embeddings.store import EmbeddingStore
from ontolib.repositories.pubmed.client import PubMedClient
from ontolib.repositories.xref.store import XrefStore
from ontolib.terminologies.ncit.graph_store import NcitGraphStore
from ontolib.terminologies.ncit.search_index import NcitSearchIndex
from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient

logger = get_logger(__name__)


async def check_ncit_version(client: OxigraphHttpClient, expected: str) -> None:
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
    """Open the SPARQL client, NCIt store, caDSR repo, and embedding store."""
    settings = get_settings()
    if not settings.api_key:
        # Surface an intended-auth misconfiguration (blank/unset key) instead of
        # silently running the mutating endpoints wide open.
        logger.warning(
            "API_KEY is not set — refresh/reload endpoints run unauthenticated "
            "(open mode). Set api_key to require X-API-Key."
        )
    client = OxigraphHttpClient(
        settings.ncit_sparql_url, query_timeout=settings.sparql_timeout_sec
    )
    engine = make_engine(settings.database_url)
    app.state.ncit_client = client
    app.state.ncit_store = NcitGraphStore(client)
    app.state.cadsr_repo = CdeRepository(settings.cadsr_db_path)
    app.state.embedding_store = EmbeddingStore(make_sessionmaker(engine))
    app.state.ncit_search_index = NcitSearchIndex(make_sessionmaker(engine))
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
    async def ready(client: NcitClient) -> dict[str, object]:
        """Readiness — the NCIt store is reachable; 503 if not."""
        try:
            version = await client.version()
        except StorageError as exc:
            # HTTPException responses aren't logged by the error handler, so log the
            # root cause here — otherwise a failing readiness probe has no server trace.
            logger.warning("Readiness check failed — NCIt store unreachable: %s", exc)
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "NCIt store not ready"
            ) from exc
        return {"ready": True, "ncit_version": version}

    app.include_router(ncit.router)
    app.include_router(mappings.router)
    app.include_router(cadsr.router)
    app.include_router(refresh.router)
    app.include_router(sparql.router)
    app.include_router(clinicaltrials.router)
    app.include_router(pubmed.router)
    app.include_router(decomposition.router)
    return app


app = create_app()
