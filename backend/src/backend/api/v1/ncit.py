"""NCIt repository read endpoints: concept detail, search, graph neighborhood,
mappings."""

import hashlib
import json
from collections.abc import Mapping
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, status
from pydantic import Field, computed_field, model_validator
from sqlalchemy.exc import SQLAlchemyError

from backend.api.v1.alignment import mapping_relative_to
from backend.config import get_settings
from backend.dependencies import (
    DecompositionReads,
    Embeddings,
    NcitSearch,
    NcitStore,
    RepositoryMetadataReads,
    XrefReads,
)
from backend.icdo_datasets import ServedIcdoDataset
from backend.repository_metadata import RepositoryUnhealthy
from backend.security import has_icdo_entitlement
from ontolib.common.boundary_models import StrictBoundaryModel
from ontolib.core.logging_config import get_logger
from ontolib.decomposition.enhanced_showcase import (
    EnhancedNcitShowcaseView,
    ShowcaseConceptNotInCohortError,
    ShowcaseConceptPolicy,
    ShowcaseConstituent,
    ShowcaseDecisionSet,
    ShowcasePolicyError,
    build_showcase_view,
    load_packaged_showcase_decision_set,
    require_active_showcase_decisions,
)
from ontolib.decomposition.read import attach_upstream, decomposition_from_rows
from ontolib.decomposition.read_models import ConceptDecomposition, UpstreamMapping
from ontolib.repositories.embeddings.publication import Corpus, CorpusUnavailableError
from ontolib.repositories.xref.models import (
    IcdoReadIdentity,
    MappingResult,
    StaleXrefGenerationError,
    UberonReadIdentity,
    UnavailableXrefGenerationError,
    XrefReadPolicy,
)
from ontolib.repositories.xref.vocab import (
    EXACT_MATCH,
    MappingLifecycle,
    MappingPredicate,
)
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


def _showcase_policy_for(
    code: str,
) -> tuple[ShowcaseDecisionSet, ShowcaseConceptPolicy]:
    try:
        policy = load_packaged_showcase_decision_set()
    except ShowcasePolicyError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    try:
        return policy, policy.concept(code)
    except ShowcaseConceptNotInCohortError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


async def _xref_expected(
    metadata: RepositoryMetadataReads, *, include_icdo: bool
) -> XrefReadPolicy:
    ncit = await metadata.ncit()
    uberon = await metadata.uberon()
    if isinstance(ncit, RepositoryUnhealthy):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, ncit.model_dump(mode="json")
        )
    if isinstance(uberon, RepositoryUnhealthy):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, uberon.model_dump(mode="json")
        )
    icdo = (
        await metadata.icdo(ServedIcdoDataset.ICDO_32_MORPHOLOGY)
        if include_icdo
        else None
    )
    if isinstance(icdo, RepositoryUnhealthy):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, icdo.model_dump(mode="json")
        )
    return XrefReadPolicy(
        uberon=UberonReadIdentity(
            ncit_source_identity=ncit.source_identity,
            uberon_source_identity=uberon.source_identity,
            uberon_serving_identity=uberon.observation.serving.sha256,
        ),
        icdo=(
            IcdoReadIdentity(
                ncit_source_identity=ncit.source_identity,
                icdo_generation_identity=icdo.activation_identity,
                icdo_serving_identity=icdo.serving_identity,
            )
            if icdo is not None
            else None
        ),
    )


class MappingEntry(StrictBoundaryModel):
    """One terminology alignment for an NCIt concept, serialized for the API."""

    object_id: str
    system: str
    version: str
    predicate: MappingPredicate
    lifecycle: MappingLifecycle
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="before")
    @classmethod
    def serialized_identity_must_match_fields(cls, value: object) -> object:
        if not isinstance(value, Mapping) or "is_identity" not in value:
            return value
        expected = value.get("predicate") == EXACT_MATCH and value.get("lifecycle") in {
            "validated",
            "active",
        }
        if value["is_identity"] is not expected:
            raise ValueError("is_identity must match predicate and lifecycle")
        without_computed = dict(value)
        without_computed.pop("is_identity")
        return without_computed

    @computed_field
    @property
    def is_identity(self) -> bool:
        """Whether this is a curated exact identity mapping."""
        return self.predicate == EXACT_MATCH and self.lifecycle in {
            "validated",
            "active",
        }


class ConceptMappings(StrictBoundaryModel):
    """Mappings plus NCIt identity; ICD-O rows require capability and entitlement."""

    code: str
    repository_source_identity: str
    repository_manifest_identity: str
    mappings: list[MappingEntry]


def _mapping_entries(
    code: str, rows: list[MappingResult], *, entitled_to_icdo: bool
) -> list[MappingEntry]:
    entries: list[MappingEntry] = []
    for row in rows:
        target, predicate = mapping_relative_to(row, code)
        if target.system == "icdo" and not entitled_to_icdo:
            continue
        entries.append(
            MappingEntry(
                object_id=target.identifier,
                system=target.system,
                version=target.version,
                predicate=predicate,
                lifecycle=row.lifecycle,
                confidence=row.confidence,
            )
        )
    return entries


async def _attach_xref_upstream(
    decomposition: ConceptDecomposition,
    xref_store: XrefReads,
    filler_codes: list[str],
    *,
    expected: XrefReadPolicy,
    entitled_to_icdo: bool,
) -> ConceptDecomposition:
    if filler_codes:
        upstream_rows = await xref_store.mappings_for_identifiers(
            set(filler_codes), expected=expected
        )
        upstream_by_filler = {
            code: [
                UpstreamMapping(
                    object_id=target.identifier,
                    predicate=predicate,
                    lifecycle=row.lifecycle,
                    confidence=row.confidence,
                )
                for row in rows
                for target, predicate in [mapping_relative_to(row, code)]
                if target.system != "icdo" or entitled_to_icdo
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
    metadata: RepositoryMetadataReads,
    code: str,
    x_icdo_entitlement: Annotated[str | None, Header()] = None,
) -> ConceptMappings:
    """Return alignments, withholding ICD-O rows without capability and entitlement.

    Searches both by subject (NCIt code as subject) and by object
    (NCIt code as object of an upstream-to-NCIt mapping), so
    ``$translate``-style round-trips are covered from this endpoint alone.
    """
    try:
        safe_iri(code, NCIT_NS)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Invalid code: {code}") from exc
    repository = await metadata.ncit()
    if isinstance(repository, RepositoryUnhealthy):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, repository.model_dump(mode="json")
        )
    entitled_to_icdo = get_settings().enable_licensed_mappings and has_icdo_entitlement(
        x_icdo_entitlement
    )
    expected = await _xref_expected(metadata, include_icdo=entitled_to_icdo)
    try:
        rows = await xref_store.mappings_for_identifiers({code}, expected=expected)
    except (StaleXrefGenerationError, UnavailableXrefGenerationError) as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    entries = _mapping_entries(
        code, rows.get(code, []), entitled_to_icdo=entitled_to_icdo
    )
    return ConceptMappings(
        code=code,
        repository_source_identity=repository.source_identity,
        repository_manifest_identity=repository.manifest_identity,
        mappings=entries,
    )


@router.get("/concepts/{code}/decomposition", response_model=ConceptDecomposition)
async def concept_decomposition(
    reader: DecompositionReads,
    store: NcitStore,
    xref_store: XrefReads,
    metadata: RepositoryMetadataReads,
    code: str,
    x_icdo_entitlement: Annotated[str | None, Header()] = None,
) -> ConceptDecomposition:
    """Return the concept's decomposition from the additive ``ncit_decomposed`` graph.

    Resolves even for a concept the engine has not decomposed
    (``is_legacy_precoordinated = false``, no constituents) so the UI can show "not
    decomposed" rather than a 404. Filler labels are resolved for display, and
    typed terminology alignments are attached per constituent. ICD-O mappings require
    the server capability and a valid ``X-ICDO-Entitlement``.
    """
    try:
        rows = await reader.rows_for(code)
    except ValueError as exc:  # code failed the IRI-safety guard
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Invalid code: {code}") from exc
    decomposition = decomposition_from_rows(code, rows)
    entitled_to_icdo = get_settings().enable_licensed_mappings and has_icdo_entitlement(
        x_icdo_entitlement
    )
    expected = await _xref_expected(metadata, include_icdo=entitled_to_icdo)
    filler_codes = [c.filler for c in decomposition.constituents]
    labels = await store.labels_for(filler_codes) if filler_codes else {}
    for constituent in decomposition.constituents:
        constituent.filler_label = labels.get(constituent.filler)
    try:
        decomposition = await _attach_xref_upstream(
            decomposition,
            xref_store,
            filler_codes,
            expected=expected,
            entitled_to_icdo=entitled_to_icdo,
        )
    except (StaleXrefGenerationError, UnavailableXrefGenerationError) as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return decomposition


@router.get(
    "/concepts/{code}/enhanced-ncit-showcase",
    response_model=EnhancedNcitShowcaseView,
)
async def concept_enhanced_ncit_showcase(
    reader: DecompositionReads,
    store: NcitStore,
    code: str,
) -> EnhancedNcitShowcaseView:
    """Return the explicit local showcase overlay without changing ordinary reads."""
    policy, concept_policy = _showcase_policy_for(code)
    try:
        base_rows = await reader.rows_for(code)
        decision_rows = await reader.showcase_rows_for(code)
        require_active_showcase_decisions(decision_rows, concept_policy.decisions)
        decomposition = decomposition_from_rows(code, base_rows)
        codes = [item.filler for item in decomposition.constituents]
        labels = await store.labels_for(codes) if codes else {}
        base = tuple(
            ShowcaseConstituent(
                axis=item.axis,
                filler=item.filler,
                label=labels.get(item.filler),
            )
            for item in decomposition.constituents
        )
        identity_payload = {
            "code": code,
            "decomposed_on": decomposition.decomposed_on,
            "constituents": [item.model_dump(mode="json") for item in base],
        }
        base_identity = hashlib.sha256(
            json.dumps(
                identity_payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("ascii")
        ).hexdigest()
        return build_showcase_view(code, base_identity, base, policy=policy)
    except (ShowcasePolicyError, ValueError) as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
