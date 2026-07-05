"""caDSR CDE repository endpoints: CDE detail, search, and the NCIt concept join."""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.exc import SQLAlchemyError

from backend.dependencies import CadsrRepo, Embeddings, NcitStore
from ontolib.repositories.cadsr.models import (
    CdeDetail,
    CdeSearchPage,
    CdeSummary,
    SimilarCde,
)
from ontolib.terminologies.ncit.graph_store import NcitGraphStore
from ontolib.terminologies.ncit.models import GraphEdge, GraphNode, Neighborhood

router = APIRouter(prefix="/api/v1/cadsr", tags=["cadsr"])

# Cap the mapped concepts expanded per CDE so a heavily-annotated CDE can't pull an
# unbounded closure (each concept also carries its own capped NCIt neighborhood).
_MAX_CDE_CONCEPTS = 12


@router.get("/search", response_model=CdeSearchPage)
def search(
    repo: CadsrRepo,
    q: Annotated[str, Query(min_length=1)],
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CdeSearchPage:
    """Search caDSR CDEs by short/long name and definition."""
    try:
        return repo.search(q, limit=limit, offset=offset)
    except sqlite3.OperationalError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


@router.get("/list", response_model=CdeSearchPage)
def list_cdes(
    repo: CadsrRepo,
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CdeSearchPage:
    """List CDEs in natural order — powers no-search browse of the repository."""
    try:
        return repo.list_cdes(limit=limit, offset=offset)
    except sqlite3.OperationalError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


@router.get("/cdes/{public_id}", response_model=CdeDetail)
def cde_detail(
    repo: CadsrRepo,
    public_id: str,
    version: Annotated[str | None, Query()] = None,
) -> CdeDetail:
    """Return a CDE with its permissible values and NCIt concept links."""
    try:
        cde = repo.get_cde(public_id, version)
    except sqlite3.OperationalError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    if cde is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"CDE not found: {public_id}")
    return cde


@router.get("/cdes/{public_id}/similar", response_model=list[SimilarCde])
async def similar_cdes(
    repo: CadsrRepo,
    embeddings: Embeddings,
    public_id: str,
    version: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> list[SimilarCde]:
    """Semantically similar CDEs via 768-dim embeddings (pgvector cosine)."""
    cde = repo.get_cde(public_id, version)  # resolve the concrete version
    if cde is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"CDE not found: {public_id}")
    try:
        hits = await embeddings.similar_cde(cde.public_id, cde.version, limit=limit)
    except SQLAlchemyError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    summaries = repo.summaries_for([doc_id for doc_id, _ in hits])
    results: list[SimilarCde] = []
    for doc_id, score in hits:
        summary = summaries.get(doc_id)
        if summary is not None:
            results.append(SimilarCde(**summary.model_dump(), score=score))
    return results


@router.get("/concepts/{concept_code}/cdes", response_model=list[CdeSummary])
def cdes_for_concept(
    repo: CadsrRepo,
    concept_code: str,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[CdeSummary]:
    """Return CDEs mapped to an NCIt concept — the caDSR↔NCIt cross-link."""
    try:
        return repo.find_cdes_by_concept(concept_code, limit=limit)
    except sqlite3.OperationalError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


@router.get("/cdes/{public_id}/neighborhood", response_model=Neighborhood)
async def cde_neighborhood(
    repo: CadsrRepo,
    store: NcitStore,
    public_id: str,
    version: Annotated[str | None, Query()] = None,
    depth: Annotated[int, Query(ge=1, le=2)] = 1,
) -> Neighborhood:
    """Return a CDE-centred subgraph joining into the NCIt concept graph.

    The CDE is a pseudo-node linked (``kind="cde-concept"``) to each mapped NCIt
    concept, and each concept carries its own NCIt neighborhood — so the graph
    explorer can launch from a data element into the ontology.
    """
    try:
        cde = repo.get_cde(public_id, version)
    except sqlite3.OperationalError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    if cde is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"CDE not found: {public_id}")
    return await _build_cde_neighborhood(cde, store, depth=depth)


_EdgeKey = tuple[str, str, str, str]


def _merge_neighborhood(
    nodes: dict[str, GraphNode],
    edges: dict[_EdgeKey, GraphEdge],
    sub: Neighborhood,
) -> None:
    """Merge a concept's NCIt neighborhood into the accumulating CDE subgraph."""
    for node in sub.nodes:
        nodes.setdefault(node.code, node)
    for edge in sub.edges:
        edges.setdefault((edge.source, edge.target, edge.relation, edge.kind), edge)


async def _build_cde_neighborhood(
    cde: CdeDetail, store: NcitGraphStore, *, depth: int
) -> Neighborhood:
    center = f"cde:{cde.public_id}:{cde.version}"
    nodes: dict[str, GraphNode] = {
        center: GraphNode(code=center, label=cde.long_name, semantic_type="CDE")
    }
    edges: dict[_EdgeKey, GraphEdge] = {}
    truncated = len(cde.concepts) > _MAX_CDE_CONCEPTS
    for link in cde.concepts[:_MAX_CDE_CONCEPTS]:
        sub = await store.get_neighborhood(link.concept_code, depth=depth)
        truncated = truncated or sub.truncated
        _merge_neighborhood(nodes, edges, sub)
        # Ensure the concept node exists even if it has no NCIt neighborhood, so the
        # CDE→concept edge never dangles.
        nodes.setdefault(
            link.concept_code,
            GraphNode(code=link.concept_code, label=link.concept_name),
        )
        edge = GraphEdge(
            source=center,
            target=link.concept_code,
            relation=link.concept_type or "hasConcept",
            kind="cde-concept",
        )
        edges.setdefault((edge.source, edge.target, edge.relation, edge.kind), edge)
    return Neighborhood(
        center=center,
        nodes=list(nodes.values()),
        edges=list(edges.values()),
        truncated=truncated,
    )
