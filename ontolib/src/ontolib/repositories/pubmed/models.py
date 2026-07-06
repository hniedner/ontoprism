"""Read models for the NCBI PubMed E-utilities client (pydantic, serialized by the API).

Ported from fairdata's dataclass models to ontoprism's pydantic convention. Covers the
subset of the ESummary/EFetch payloads needed to search PubMed, show an article, and
list related articles — direct search only (no LLM query building / reranking).
"""

from pydantic import BaseModel


class PubMedAuthor(BaseModel):
    """An article author (name parts as PubMed records them)."""

    last_name: str | None = None
    fore_name: str | None = None
    initials: str | None = None


class MeshTerm(BaseModel):
    """A MeSH heading with its qualifiers and major-topic flag."""

    descriptor: str
    qualifiers: list[str] = []
    major_topic: bool = False


class PubMedArticleSummary(BaseModel):
    """A lightweight article reference for search-result tables (from ESummary)."""

    pmid: str
    title: str
    journal: str | None = None
    pub_date: str | None = None
    authors: list[str] = []
    doi: str | None = None


class PubMedArticleDetail(BaseModel):
    """Full article detail assembled from EFetch XML."""

    pmid: str
    title: str
    abstract: str | None = None
    authors: list[PubMedAuthor] = []
    journal: str | None = None
    pub_date: str | None = None
    doi: str | None = None
    pmc_id: str | None = None
    mesh_terms: list[MeshTerm] = []
    keywords: list[str] = []
    url: str = ""


class PubMedSearchResult(BaseModel):
    """A page of PubMed search results (ESearch id list resolved via ESummary)."""

    query: str
    total: int
    articles: list[PubMedArticleSummary] = []


class RelatedArticlesResult(BaseModel):
    """Related-article PMIDs for a source article (from ELink)."""

    pmid: str
    link_type: str
    related_pmids: list[str] = []
