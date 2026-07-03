"""FastAPI application entrypoint.

M0 provides only the app factory and a liveness probe. The repository/graph/search/
sparql/refresh routers are lifted in later milestones.
"""

from fastapi import FastAPI

from backend import __version__


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    app = FastAPI(title="ontoprism", version=__version__)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
