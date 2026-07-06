"""Read models for the ClinicalTrials.gov API v2 (pydantic, serialized by the API).

Ported from fairdata's dataclass models to ontoprism's pydantic convention. These
mirror the subset of the ClinicalTrials.gov v2 ``protocolSection`` module tree that
the client parses — enough to browse trials and open a trial detail.
"""

from pydantic import BaseModel


class CTInterventionDetail(BaseModel):
    """An intervention (drug, procedure, device, …) evaluated by a trial."""

    type: str | None = None
    name: str
    description: str | None = None


class CTOutcome(BaseModel):
    """A primary or secondary outcome measure."""

    measure: str
    description: str | None = None
    time_frame: str | None = None


class CTSponsor(BaseModel):
    """A trial sponsor or collaborator."""

    name: str
    role: str | None = None  # "lead" | "collaborator"


class CTLocation(BaseModel):
    """A facility where the trial is (or was) conducted."""

    facility: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    status: str | None = None


class CTReference(BaseModel):
    """A publication referenced by a trial (the CT.gov↔PubMed cross-link)."""

    pmid: str | None = None
    citation: str
    reference_type: str | None = None  # "RESULT" | "BACKGROUND" | "DERIVED"


class CTStudySummary(BaseModel):
    """A lightweight trial reference for search-result tables."""

    nct_id: str
    title: str
    status: str | None = None
    phase: str | None = None
    conditions: list[str] = []
    interventions: list[str] = []
    start_date: str | None = None
    enrollment: int | None = None
    # Synthesized from result position (the CT.gov API returns no relevance score).
    relevance_score: float = 0.0


class CTStudyDetail(BaseModel):
    """Full trial detail assembled from the CT.gov v2 protocol-section modules."""

    nct_id: str
    title: str
    official_title: str | None = None
    status: str | None = None
    phase: str | None = None
    study_type: str | None = None
    primary_purpose: str | None = None
    conditions: list[str] = []
    interventions: list[CTInterventionDetail] = []
    primary_outcomes: list[CTOutcome] = []
    secondary_outcomes: list[CTOutcome] = []
    eligibility_criteria: str | None = None
    enrollment: int | None = None
    start_date: str | None = None
    sponsors: list[CTSponsor] = []
    locations: list[CTLocation] = []
    references: list[CTReference] = []
    url: str = ""


class CTStudySearchPage(BaseModel):
    """A page of trial search results with the resolved query terms echoed back."""

    condition: str | None = None
    intervention: str | None = None
    term: str | None = None
    total: int
    studies: list[CTStudySummary] = []
