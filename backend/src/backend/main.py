"""FastAPI application entrypoint.

Owns the process-wide Oxigraph SPARQL client (opened for the app lifespan) and the
NCIt repository read model; the frontend talks only to this backend.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend import __version__
from backend.api.v1 import cadsr, ncit, sparql
from backend.config import get_settings
from fairlib.repositories.cadsr.repository import CdeRepository
from fairlib.terminologies.ncit.graph_store import NcitGraphStore
from fairlib.terminologies.oxigraph_http_client import OxigraphHttpClient


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the shared SPARQL client, NCIt store, and caDSR repo; close on shutdown."""
    settings = get_settings()
    client = OxigraphHttpClient(
        settings.ncit_sparql_url, query_timeout=settings.sparql_timeout_sec
    )
    app.state.ncit_client = client
    app.state.ncit_store = NcitGraphStore(client)
    app.state.cadsr_repo = CdeRepository(settings.cadsr_db_path)
    try:
        yield
    finally:
        await client.aclose()


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    app = FastAPI(title="ontoprism", version=__version__, lifespan=lifespan)

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    app.include_router(ncit.router)
    app.include_router(cadsr.router)
    app.include_router(sparql.router)
    return app


app = create_app()
