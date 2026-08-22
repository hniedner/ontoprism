"""Read models for the NCBI PubMed E-utilities client (pydantic, serialized by the API).

Ported from fairdata's dataclass models to ontoprism's pydantic convention. Covers the
subset of the ESummary/EFetch payloads needed to search PubMed, show an article, and
list related articles — direct search only (no LLM query building / reranking).
"""

from pydantic import Field

from ontolib.common.boundary_models import StrictBoundaryModel


class PubMedAuthor(StrictBoundaryModel):
    """An article author (name parts as PubMed records them)."""

    last_name: str | None = None
    fore_name: str | None = None
    initials: str | None = None


class MeshTerm(StrictBoundaryModel):
    """A MeSH heading with its qualifiers and major-topic flag."""

    descriptor: str
    qualifiers: list[str] = Field(default_factory=list)
    major_topic: bool = False


class PubMedArticleSummary(StrictBoundaryModel):
    """A lightweight article reference for search-result tables (from ESummary)."""

    pmid: str
    title: str
    journal: str | None = None
    pub_date: str | None = None
    authors: list[str] = Field(default_factory=list)
    doi: str | None = None


class PubMedArticleDetail(StrictBoundaryModel):
    """Full article detail assembled from EFetch XML."""

    pmid: str
    title: str
    abstract: str | None = None
    authors: list[PubMedAuthor] = Field(default_factory=list)
    journal: str | None = None
    pub_date: str | None = None
    doi: str | None = None
    pmc_id: str | None = None
    mesh_terms: list[MeshTerm] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    url: str = ""


class PubMedSearchResult(StrictBoundaryModel):
    """A page of PubMed search results (ESearch id list resolved via ESummary)."""

    query: str
    total: int
    articles: list[PubMedArticleSummary] = Field(default_factory=list)


class RelatedArticlesResult(StrictBoundaryModel):
    """Related-article PMIDs for a source article (from ELink)."""

    pmid: str
    link_type: str
    related_pmids: list[str] = Field(default_factory=list)
