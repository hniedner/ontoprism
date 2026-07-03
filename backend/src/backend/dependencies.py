"""FastAPI dependencies: access to the shared NCIt store held on app state."""

from typing import Annotated

from fastapi import Depends, Request

from fairlib.terminologies.ncit.graph_store import NcitGraphStore
from fairlib.terminologies.oxigraph_http_client import OxigraphHttpClient


def get_ncit_store(request: Request) -> NcitGraphStore:
    """Return the process-wide NCIt store created during app startup."""
    return request.app.state.ncit_store


def get_ncit_client(request: Request) -> OxigraphHttpClient:
    """Return the process-wide NCIt SPARQL client."""
    return request.app.state.ncit_client


NcitStore = Annotated[NcitGraphStore, Depends(get_ncit_store)]
NcitClient = Annotated[OxigraphHttpClient, Depends(get_ncit_client)]
