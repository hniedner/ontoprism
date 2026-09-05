"""Read models for the ClinicalTrials.gov API v2 (pydantic, serialized by the API).

Ported from fairdata's dataclass models to ontoprism's pydantic convention. These
mirror the subset of the ClinicalTrials.gov v2 ``protocolSection`` module tree that
the client parses — enough to browse trials and open a trial detail.
"""

from typing import Literal

from pydantic import Field

from ontolib.common.boundary_models import StrictBoundaryModel

CTPageSize = Literal[10, 25, 50, 100]
CTStatus = Literal[
    "ACTIVE_NOT_RECRUITING",
    "APPROVED_FOR_MARKETING",
    "AVAILABLE",
    "COMPLETED",
    "ENROLLING_BY_INVITATION",
    "NOT_YET_RECRUITING",
    "NO_LONGER_AVAILABLE",
    "RECRUITING",
    "SUSPENDED",
    "TEMPORARILY_NOT_AVAILABLE",
    "TERMINATED",
    "UNKNOWN",
    "WITHDRAWN",
    "WITHHELD",
]
CTPhase = Literal["EARLY_PHASE1", "PHASE1", "PHASE2", "PHASE3", "PHASE4"]


class CTInterventionDetail(StrictBoundaryModel):
    """An intervention (drug, procedure, device, …) evaluated by a trial."""

    type: str | None = None
    name: str
    description: str | None = None


class CTOutcome(StrictBoundaryModel):
    """A primary or secondary outcome measure."""

    measure: str
    description: str | None = None
    time_frame: str | None = None


class CTSponsor(StrictBoundaryModel):
    """A trial sponsor or collaborator."""

    name: str
    role: str | None = None  # "lead" | "collaborator"


class CTLocation(StrictBoundaryModel):
    """A facility where the trial is (or was) conducted."""

    facility: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    status: str | None = None


class CTReference(StrictBoundaryModel):
    """A publication referenced by a trial (the CT.gov↔PubMed cross-link)."""

    pmid: str | None = None
    citation: str
    reference_type: str | None = None  # "RESULT" | "BACKGROUND" | "DERIVED"


class CTStudySummary(StrictBoundaryModel):
    """A lightweight trial reference for search-result tables."""

    nct_id: str
    title: str
    status: str | None = None
    phase: str | None = None
    conditions: list[str] = Field(default_factory=list)
    interventions: list[str] = Field(default_factory=list)
    start_date: str | None = None
    enrollment: int | None = None
    # Synthesized from result position (the CT.gov API returns no relevance score).
    relevance_score: float = 0.0


class CTStudyDetail(StrictBoundaryModel):
    """Full trial detail assembled from the CT.gov v2 protocol-section modules."""

    nct_id: str
    title: str
    official_title: str | None = None
    status: str | None = None
    phase: str | None = None
    study_type: str | None = None
    primary_purpose: str | None = None
    conditions: list[str] = Field(default_factory=list)
    interventions: list[CTInterventionDetail] = Field(default_factory=list)
    primary_outcomes: list[CTOutcome] = Field(default_factory=list)
    secondary_outcomes: list[CTOutcome] = Field(default_factory=list)
    eligibility_criteria: str | None = None
    enrollment: int | None = None
    start_date: str | None = None
    sponsors: list[CTSponsor] = Field(default_factory=list)
    locations: list[CTLocation] = Field(default_factory=list)
    references: list[CTReference] = Field(default_factory=list)
    url: str = ""


class CTStudySearchPage(StrictBoundaryModel):
    """A page of trial search results with the resolved query terms echoed back."""

    condition: str | None = None
    intervention: str | None = None
    term: str | None = None
    total: int
    page_size: CTPageSize
    page_token: str | None = None
    next_page_token: str | None = None
    studies: list[CTStudySummary] = Field(default_factory=list)
