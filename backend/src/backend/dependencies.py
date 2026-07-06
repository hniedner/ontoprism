"""FastAPI dependencies: access to the shared NCIt store held on app state."""

from typing import Annotated

from fastapi import Depends, Request

from ontolib.repositories.cadsr.repository import CdeRepository
from ontolib.repositories.clinicaltrials.client import ClinicalTrialsClient
from ontolib.repositories.embeddings.store import EmbeddingStore
from ontolib.terminologies.ncit.graph_store import NcitGraphStore
from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient


def get_ncit_store(request: Request) -> NcitGraphStore:
    """Return the process-wide NCIt store created during app startup."""
    return request.app.state.ncit_store


def get_ncit_client(request: Request) -> OxigraphHttpClient:
    """Return the process-wide NCIt SPARQL client."""
    return request.app.state.ncit_client


def get_cadsr_repo(request: Request) -> CdeRepository:
    """Return the process-wide caDSR CDE repository."""
    return request.app.state.cadsr_repo


def get_embedding_store(request: Request) -> EmbeddingStore:
    """Return the process-wide pgvector embedding store."""
    return request.app.state.embedding_store


def get_clinicaltrials_client(request: Request) -> ClinicalTrialsClient:
    """Return the process-wide ClinicalTrials.gov API client."""
    return request.app.state.clinicaltrials_client


NcitStore = Annotated[NcitGraphStore, Depends(get_ncit_store)]
NcitClient = Annotated[OxigraphHttpClient, Depends(get_ncit_client)]
CadsrRepo = Annotated[CdeRepository, Depends(get_cadsr_repo)]
Embeddings = Annotated[EmbeddingStore, Depends(get_embedding_store)]
ClinicalTrials = Annotated[ClinicalTrialsClient, Depends(get_clinicaltrials_client)]
