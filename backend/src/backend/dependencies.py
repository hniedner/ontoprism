"""FastAPI dependencies: access to the shared NCIt store held on app state."""

from typing import Annotated

from fastapi import Depends, Request

from ontolib.repositories.cadsr.repository import CdeRepository
from ontolib.repositories.clinicaltrials.client import ClinicalTrialsClient
from ontolib.repositories.embeddings.store import EmbeddingStore
from ontolib.repositories.pubmed.client import PubMedClient
from ontolib.terminologies.ncit.graph_store import NcitGraphStore
from ontolib.terminologies.ncit.search_index import NcitSearchIndex
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


def get_pubmed_client(request: Request) -> PubMedClient:
    """Return the process-wide PubMed E-utilities client."""
    return request.app.state.pubmed_client


def get_ncit_search_index(request: Request) -> NcitSearchIndex:
    """Return the process-wide NCIt FTS search index."""
    return request.app.state.ncit_search_index


NcitStore = Annotated[NcitGraphStore, Depends(get_ncit_store)]
NcitClient = Annotated[OxigraphHttpClient, Depends(get_ncit_client)]
CadsrRepo = Annotated[CdeRepository, Depends(get_cadsr_repo)]
Embeddings = Annotated[EmbeddingStore, Depends(get_embedding_store)]
ClinicalTrials = Annotated[ClinicalTrialsClient, Depends(get_clinicaltrials_client)]
PubMed = Annotated[PubMedClient, Depends(get_pubmed_client)]
NcitSearch = Annotated[NcitSearchIndex, Depends(get_ncit_search_index)]
