"""NCIt repository read endpoints: concept detail, search, graph neighborhood,
mappings."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, computed_field
from sqlalchemy.exc import SQLAlchemyError

from backend.dependencies import (
    DecompositionReads,
    Embeddings,
    NcitSearch,
    NcitStore,
    RepositoryMetadataReads,
    XrefReads,
)
from backend.repository_metadata import RepositoryUnhealthy
from ontolib.core.logging_config import get_logger
from ontolib.decomposition.read import attach_upstream, decomposition_from_rows
from ontolib.decomposition.read_models import ConceptDecomposition, UpstreamMapping
from ontolib.repositories.embeddings.publication import Corpus, CorpusUnavailableError
from ontolib.repositories.xref.vocab import EXACT_MATCH
from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.ncit.models import (
    ConceptDetail,
    Neighborhood,
    RepresentationStatus,
    SearchPage,
    SimilarConcept,
)
from ontolib.terminologies.sparql_transport import safe_iri

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/ncit", tags=["ncit"])


class MappingEntry(BaseModel):
    """One upstream mapping for an NCIt concept, serialized for the API.

    ``is_identity`` mirrors ``UpstreamMapping.is_identity``: true when
    the predicate is ``exactMatch`` and lifecycle is ``validated``/``active``.
    """

    object_id: str
    system: str
    version: str
    predicate: str
    lifecycle: str
    confidence: float = Field(ge=0.0, le=1.0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_identity(self) -> bool:
        return self.predicate == EXACT_MATCH and self.lifecycle in (
            "validated",
            "active",
        )


class ConceptMappings(BaseModel):
    """All upstream mappings for one NCIt concept code."""

    code: str
    mappings: list[MappingEntry]


async def _attach_xref_upstream(
    decomposition: ConceptDecomposition,
    xref_store: XrefReads,
    filler_codes: list[str],
) -> ConceptDecomposition:
    if filler_codes:
        upstream_rows = await xref_store.mappings_by_subjects(set(filler_codes))
        upstream_by_filler = {
            code: [
                UpstreamMapping(
                    object_id=row.object.identifier,
                    predicate=row.predicate,
                    lifecycle=row.lifecycle,
                    confidence=row.confidence,
                )
                for row in rows
            ]
            for code, rows in upstream_rows.items()
        }
        decomposition = attach_upstream(decomposition, upstream_by_filler)
    return decomposition


@router.get("/search", response_model=SearchPage)
async def search(
    store: NcitStore,
    index: NcitSearch,
    metadata: RepositoryMetadataReads,
    q: Annotated[str, Query(min_length=1, description="Search term")],
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
    representation_status: Annotated[
        RepresentationStatus | None,
        Query(description="Published representation status"),
    ] = None,
) -> SearchPage:
    """Search NCIt by label/synonyms; served from the FTS cache when populated.

    Falls back to the live SPARQL scan when the cache is empty or unreachable, so
    search always works (the store remains the source of truth).
    """
    repository = await metadata.ncit()
    if isinstance(repository, RepositoryUnhealthy):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            repository.model_dump(mode="json"),
        )
    try:
        if await index.is_populated(repository.source_identity):
            return await index.search(
                q,
                limit=limit,
                offset=offset,
                representation_status=representation_status,
            )
    except SQLAlchemyError as exc:
        logger.warning("NCIt FTS cache unavailable, falling back to SPARQL: %s", exc)
    return await store.search(
        q,
        limit=limit,
        offset=offset,
        representation_status=representation_status,
    )


@router.get("/list", response_model=SearchPage)
async def list_concepts(
    store: NcitStore,
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
    representation_status: Annotated[
        RepresentationStatus | None,
        Query(description="Published representation status"),
    ] = None,
) -> SearchPage:
    """List concepts in natural order — powers no-search browse of the repository."""
    return await store.list_concepts(
        limit=limit,
        offset=offset,
        representation_status=representation_status,
    )


@router.get("/concepts/{code}", response_model=ConceptDetail)
async def concept_detail(store: NcitStore, code: str) -> ConceptDetail:
    """Return full concept detail — parents, roles, associations, incoming roles."""
    try:
        detail = await store.get_concept_detail(code)
    except ValueError as exc:  # malformed code rejected by the IRI guard
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Invalid code: {code}") from exc
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Concept not found: {code}")
    return detail


@router.get("/concepts/{code}/similar", response_model=list[SimilarConcept])
async def similar_concepts(
    store: NcitStore,
    embeddings: Embeddings,
    metadata: RepositoryMetadataReads,
    code: str,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> list[SimilarConcept]:
    """Semantically similar concepts via 768-dim embeddings (pgvector cosine)."""
    repository = await metadata.ncit()
    if isinstance(repository, RepositoryUnhealthy):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            repository.model_dump(mode="json"),
        )
    try:
        await embeddings.require_active_source(Corpus.NCIT, repository.source_identity)
        build_id = await embeddings.active_build_id(Corpus.NCIT)
        hits = await embeddings.similar_ncit(code, limit=limit)
    except (SQLAlchemyError, CorpusUnavailableError) as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    labels = await store.labels_for([c for c, _ in hits])
    try:
        await embeddings.require_same_active_build(Corpus.NCIT, build_id)
    except CorpusUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return [
        SimilarConcept(code=c, label=labels.get(c), score=score) for c, score in hits
    ]


@router.get("/concepts/{code}/neighborhood", response_model=Neighborhood)
async def neighborhood(
    store: NcitStore,
    code: str,
    depth: Annotated[int, Query(ge=1, le=3)] = 1,
) -> Neighborhood:
    """Return a concept-centered subgraph for the graph explorer (expand-on-demand)."""
    try:
        return await store.get_neighborhood(code, depth=depth)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Invalid code: {code}") from exc


@router.get("/concepts/{code}/mappings", response_model=ConceptMappings)
async def concept_mappings(
    store: NcitStore,
    xref_store: XrefReads,
    code: str,
) -> ConceptMappings:
    """Return all upstream mappings for an NCIt concept code.

    Searches both by subject (NCIt code as subject) and by object
    (NCIt code as object of an upstream-to-NCIt mapping), so
    ``$translate``-style round-trips are covered from this endpoint alone.
    """
    try:
        safe_iri(code, NCIT_NS)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Invalid code: {code}") from exc
    rows = await xref_store.mappings_for_identifiers({code})
    entries = [
        MappingEntry(
            object_id=target.identifier,
            system=target.system,
            version=target.version,
            predicate=row.predicate,
            lifecycle=row.lifecycle,
            confidence=row.confidence,
        )
        for row in rows.get(code, [])
        for target in [row.object if row.subject.identifier == code else row.subject]
    ]
    return ConceptMappings(code=code, mappings=entries)


@router.get("/concepts/{code}/decomposition", response_model=ConceptDecomposition)
async def concept_decomposition(
    reader: DecompositionReads,
    store: NcitStore,
    xref_store: XrefReads,
    code: str,
) -> ConceptDecomposition:
    """Return the concept's decomposition from the additive ``ncit_decomposed`` graph.

    Resolves even for a concept the engine has not decomposed
    (``is_legacy_precoordinated = false``, no constituents) so the UI can show "not
    decomposed" rather than a 404. Filler labels are resolved for display, and
    upstream xref mappings (Uberon/CL equivalents) are attached per constituent.
    """
    try:
        rows = await reader.rows_for(code)
    except ValueError as exc:  # code failed the IRI-safety guard
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Invalid code: {code}") from exc
    decomposition = decomposition_from_rows(code, rows)
    filler_codes = [c.filler for c in decomposition.constituents]
    labels = await store.labels_for(filler_codes) if filler_codes else {}
    for constituent in decomposition.constituents:
        constituent.filler_label = labels.get(constituent.filler)
    decomposition = await _attach_xref_upstream(decomposition, xref_store, filler_codes)
    return decomposition
