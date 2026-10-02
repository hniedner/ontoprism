"""caDSR CDE repository endpoints: CDE detail, search, and the NCIt concept join."""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from backend.api.v1.grid import GridService, PageSize, declared_grid, present_text
from backend.dependencies import (
    CadsrRepo,
    Embeddings,
    NcitStore,
    RepositoryMetadataReads,
)
from backend.repository_metadata import CadsrRepositoryReady
from ontolib.common.grid import ColumnText
from ontolib.repositories.cadsr.models import (
    CdeDetail,
    CdeRepositorySort,
    CdeSearchPage,
    CdeSummary,
    SimilarCde,
)
from ontolib.repositories.embeddings.publication import Corpus, CorpusUnavailableError
from ontolib.terminologies.ncit.graph_store import (
    MAX_NEIGHBORHOOD_CENTERS,
    NcitGraphStore,
)
from ontolib.terminologies.ncit.models import GraphEdge, GraphNode, Neighborhood

router = APIRouter(prefix="/api/v1/cadsr", tags=["cadsr"])

_FILTER_DOMAINS = {
    "value_domain_type": "value-domain-types",
    "workflow_status": "workflow-statuses",
    "registration_status": "registration-statuses",
    "context": "contexts",
    "datatype": "datatypes",
}


def _column_text(
    public_id_text: ColumnText | None = None,
    name_text: ColumnText | None = None,
    value_domain_type_text: ColumnText | None = None,
    workflow_status_text: ColumnText | None = None,
    registration_status_text: ColumnText | None = None,
    context_text: ColumnText | None = None,
    datatype_text: ColumnText | None = None,
) -> dict[str, str]:
    return present_text(
        public_id=public_id_text,
        name=name_text,
        value_domain_type=value_domain_type_text,
        workflow_status=workflow_status_text,
        registration_status=registration_status_text,
        context=context_text,
        datatype=datatype_text,
    )


def _filters(
    value_domain_type: Annotated[list[str] | None, Query()] = None,
    workflow_status: Annotated[list[str] | None, Query()] = None,
    registration_status: Annotated[list[str] | None, Query()] = None,
    context: Annotated[list[str] | None, Query()] = None,
    datatype: Annotated[list[str] | None, Query()] = None,
) -> dict[str, list[str]]:
    return {
        key: value
        for key, value in {
            "value_domain_type": value_domain_type,
            "workflow_status": workflow_status,
            "registration_status": registration_status,
            "context": context,
            "datatype": datatype,
        }.items()
        if value is not None
    }


CadsrColumnText = Annotated[dict[str, str], Depends(_column_text)]
CadsrFilters = Annotated[dict[str, list[str]], Depends(_filters)]


def _grid(
    repo: CadsrRepo, metadata: RepositoryMetadataReads
) -> GridService[CadsrRepositoryReady]:
    async def filter_domain(repository: CadsrRepositoryReady, field: str) -> list[str]:
        domains = await run_in_threadpool(
            repo.filter_domains, repository.manifest_identity
        )
        return domains[field]

    domains = {
        domain: lambda repository, field=field: filter_domain(repository, field)
        for field, domain in _FILTER_DOMAINS.items()
    }
    return declared_grid("cadsr", metadata.cadsr, domains)


CadsrGrid = Annotated[GridService[CadsrRepositoryReady], Depends(_grid)]


def _resolve_similar_cdes(
    repo: CadsrRepo, hits: list[tuple[str, float]]
) -> list[SimilarCde]:
    summaries = repo.summaries_for([doc_id for doc_id, _ in hits])
    unresolved = [doc_id for doc_id, _ in hits if doc_id not in summaries]
    if unresolved:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "active caDSR embeddings do not match the current source: "
            + ", ".join(unresolved),
        )
    return [
        SimilarCde(**summaries[doc_id].model_dump(), score=score)
        for doc_id, score in hits
    ]


@router.get("/filter-domains")
async def filter_domains(grid: CadsrGrid) -> dict[str, list[str]]:
    """Return source spellings for the declared caDSR closed domains."""
    return {field: await grid.filter_domain(field) for field in _FILTER_DOMAINS}


@router.get("/search", response_model=CdeSearchPage)
async def search(
    repo: CadsrRepo,
    grid: CadsrGrid,
    q: Annotated[str, Query(min_length=1)],
    column_text: CadsrColumnText,
    filters: CadsrFilters,
    limit: PageSize = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: CdeRepositorySort = "source",
) -> CdeSearchPage:
    """Search caDSR CDEs by short/long name and definition."""
    await grid.validate("search", sort, column_text, filters)
    return await grid.read_sync(
        lambda _: repo.search(
            q,
            limit=limit,
            offset=offset,
            sort=sort,
            filters=filters,
            column_text=column_text,
        )
    )


@router.get("/list", response_model=CdeSearchPage)
async def list_cdes(
    repo: CadsrRepo,
    grid: CadsrGrid,
    column_text: CadsrColumnText,
    filters: CadsrFilters,
    limit: PageSize = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: CdeRepositorySort = "source",
) -> CdeSearchPage:
    """List CDEs in the requested closed, deterministic sort order."""
    await grid.validate("list", sort, column_text, filters)
    return await grid.read_sync(
        lambda _: repo.list_cdes(
            limit=limit,
            offset=offset,
            sort=sort,
            filters=filters,
            column_text=column_text,
        )
    )


@router.get("/cdes/{public_id}", response_model=CdeDetail)
async def cde_detail(
    repo: CadsrRepo,
    grid: CadsrGrid,
    public_id: str,
    version: Annotated[str | None, Query()] = None,
) -> CdeDetail:
    """Return a CDE with its permissible values and NCIt concept links."""
    return await grid.read_detail_sync(
        lambda _: repo.get_cde(public_id, version), detail=public_id, noun="CDE"
    )


@router.get("/cdes/{public_id}/similar", response_model=list[SimilarCde])
async def similar_cdes(
    repo: CadsrRepo,
    embeddings: Embeddings,
    grid: CadsrGrid,
    public_id: str,
    version: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> list[SimilarCde]:
    """Semantically similar CDEs via 768-dim embeddings (pgvector cosine)."""
    repository = await grid.ready()
    cde = await grid.read_detail_sync(
        lambda _: repo.get_cde(public_id, version), detail=public_id, noun="CDE"
    )
    try:
        await embeddings.require_active_source(Corpus.CADSR, repository.source_identity)
        build_id = await embeddings.active_build_id(Corpus.CADSR)
        hits = await embeddings.similar_cde(cde.public_id, cde.version, limit=limit)
    except (SQLAlchemyError, CorpusUnavailableError) as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    results = _resolve_similar_cdes(repo, hits)
    try:
        await embeddings.require_same_active_build(Corpus.CADSR, build_id)
    except CorpusUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
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
    return await _build_cde_neighborhood(cde, store)


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
    cde: CdeDetail, store: NcitGraphStore
) -> Neighborhood:
    center = f"cde:{cde.public_id}:{cde.version}"
    nodes: dict[str, GraphNode] = {
        center: GraphNode(code=center, label=cde.long_name, semantic_type="CDE")
    }
    edges: dict[_EdgeKey, GraphEdge] = {}
    truncated = len(cde.concepts) > MAX_NEIGHBORHOOD_CENTERS
    links = cde.concepts[:MAX_NEIGHBORHOOD_CENTERS]
    subgraphs = await store.get_neighborhoods([link.concept_code for link in links])
    for link, sub in zip(links, subgraphs, strict=True):
        truncated = truncated or sub.truncated
        _merge_neighborhood(nodes, edges, sub)
        # Keep the CDE→concept edge even when NCIt has no neighborhood.
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
