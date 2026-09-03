"""Read models for the caDSR CDE repository (pydantic, serialized by the API)."""

from pydantic import Field

from ontolib.common.boundary_models import StrictBoundaryModel


class ConceptLink(StrictBoundaryModel):
    """A CDE's link to an NCIt concept — the shared identity that joins the graphs."""

    concept_code: str
    concept_name: str
    concept_type: str | None = None
    is_primary: bool = False


class PermissibleValue(StrictBoundaryModel):
    """A permissible value in a CDE's enumerated value domain."""

    value: str
    meaning: str | None = None
    meaning_code: str | None = None


class CdeSummary(StrictBoundaryModel):
    """A lightweight CDE reference for tables and join results."""

    public_id: str
    version: str
    short_name: str
    long_name: str
    context: str | None = None
    datatype: str | None = None


class CdeDetail(CdeSummary):
    """Full CDE detail, including its NCIt concept links and permissible values."""

    definition: str | None = None
    workflow_status: str | None = None
    registration_status: str | None = None
    value_domain_type: str | None = None
    permissible_values: list[PermissibleValue] = Field(default_factory=list)
    concepts: list[ConceptLink] = Field(default_factory=list)


class SimilarCde(CdeSummary):
    """A CDE semantically similar to another (cosine over 768-dim embeddings)."""

    score: float


class CdeSearchPage(StrictBoundaryModel):
    """A paginated CDE search result."""

    query: str
    total: int
    limit: int
    offset: int
    hits: list[CdeSummary] = Field(default_factory=list)
