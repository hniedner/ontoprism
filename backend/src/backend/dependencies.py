"""FastAPI dependencies: access to the shared NCIt store held on app state."""

from typing import Annotated

from fastapi import Depends, Request

from fairlib.repositories.cadsr.repository import CdeRepository
from fairlib.terminologies.ncit.graph_store import NcitGraphStore
from fairlib.terminologies.oxigraph_http_client import OxigraphHttpClient


def get_ncit_store(request: Request) -> NcitGraphStore:
    """Return the process-wide NCIt store created during app startup."""
    return request.app.state.ncit_store


def get_ncit_client(request: Request) -> OxigraphHttpClient:
    """Return the process-wide NCIt SPARQL client."""
    return request.app.state.ncit_client


def get_cadsr_repo(request: Request) -> CdeRepository:
    """Return the process-wide caDSR CDE repository."""
    return request.app.state.cadsr_repo


NcitStore = Annotated[NcitGraphStore, Depends(get_ncit_store)]
NcitClient = Annotated[OxigraphHttpClient, Depends(get_ncit_client)]
CadsrRepo = Annotated[CdeRepository, Depends(get_cadsr_repo)]
