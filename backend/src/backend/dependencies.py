"""FastAPI dependencies: access to the shared NCIt store held on app state."""

from typing import Annotated, Protocol

from fastapi import Depends, Request

from backend.decomposition_reader import DecompositionReader
from backend.repository_metadata import (
    CadsrRepositoryReady,
    NcitRepositoryReady,
    RepositoryUnhealthy,
)
from ontolib.decomposition.provenance import ProvenanceStore
from ontolib.repositories.cadsr.repository import CdeRepository
from ontolib.repositories.clinicaltrials.client import ClinicalTrialsClient
from ontolib.repositories.embeddings.store import EmbeddingStore
from ontolib.repositories.pubmed.client import PubMedClient
from ontolib.repositories.xref.store import XrefStore
from ontolib.terminologies.ncit.graph_store import NcitGraphStore
from ontolib.terminologies.ncit.search_index import NcitSearchIndex
from ontolib.terminologies.sparql_http_client import SparqlHttpClient


def get_ncit_store(request: Request) -> NcitGraphStore:
    """Return the process-wide NCIt store created during app startup."""
    return request.app.state.ncit_store


def get_ncit_client(request: Request) -> SparqlHttpClient:
    """Return the process-wide NCIt SPARQL client."""
    return request.app.state.ncit_client


def get_decomposition_reader(request: Request) -> DecompositionReader:
    """Return the code-based decomposition reader created during app startup."""
    return request.app.state.decomposition_reader


def get_cadsr_repo(request: Request) -> CdeRepository:
    """Return the process-wide caDSR CDE repository."""
    return request.app.state.cadsr_repo


def get_embedding_store(
    request: Request,
) -> EmbeddingStore:
    """Return the process-wide pgvector embedding store."""
    return request.app.state.embedding_store


def get_clinicaltrials_client(request: Request) -> ClinicalTrialsClient:
    """Return the process-wide ClinicalTrials.gov API client."""
    return request.app.state.clinicaltrials_client


def get_pubmed_client(request: Request) -> PubMedClient:
    """Return the process-wide PubMed E-utilities client."""
    return request.app.state.pubmed_client


def get_provenance_store(
    request: Request,
) -> ProvenanceStore:
    """Return the process-wide decomposition provenance store."""
    return request.app.state.provenance_store


def get_ncit_search_index(
    request: Request,
) -> NcitSearchIndex:
    """Return the process-wide NCIt FTS search index."""
    return request.app.state.ncit_search_index


def get_xref_store(
    request: Request,
) -> XrefStore:
    """Return the process-wide xref mapping store."""
    return request.app.state.xref_store


class RepositoryMetadataReader(Protocol):
    """Read exact, certified identities for the active repository proxies."""

    async def ncit(self) -> NcitRepositoryReady | RepositoryUnhealthy: ...

    def cadsr(self) -> CadsrRepositoryReady | RepositoryUnhealthy: ...


def get_repository_metadata(request: Request) -> RepositoryMetadataReader:
    """Return the process-wide repository certification service."""
    return request.app.state.repository_metadata


class NcitStatusClient(Protocol):
    async def count(self) -> int: ...

    async def version(self) -> str | None: ...


NcitStore = Annotated[NcitGraphStore, Depends(get_ncit_store)]
NcitStatus = Annotated[NcitStatusClient, Depends(get_ncit_client)]
DecompositionReads = Annotated[DecompositionReader, Depends(get_decomposition_reader)]
CadsrRepo = Annotated[CdeRepository, Depends(get_cadsr_repo)]
Embeddings = Annotated[EmbeddingStore, Depends(get_embedding_store)]
ClinicalTrials = Annotated[ClinicalTrialsClient, Depends(get_clinicaltrials_client)]
PubMed = Annotated[PubMedClient, Depends(get_pubmed_client)]
NcitSearch = Annotated[NcitSearchIndex, Depends(get_ncit_search_index)]
ProvenanceReads = Annotated[ProvenanceStore, Depends(get_provenance_store)]
XrefReads = Annotated[XrefStore, Depends(get_xref_store)]
RepositoryMetadataReads = Annotated[
    RepositoryMetadataReader, Depends(get_repository_metadata)
]
