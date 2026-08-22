"""Read models for the NCIt repository (pydantic, serialized directly by the API)."""

from typing import Literal

from pydantic import Field

from ontolib.common.boundary_models import StrictBoundaryModel

RepresentationStatus = Literal["legacy-precoordinated"]


class ConceptRef(StrictBoundaryModel):
    """A lightweight reference to a concept: its code and best-available label."""

    code: str
    label: str | None = None


class Relationship(StrictBoundaryModel):
    """A typed edge from a concept to a target concept.

    ``relation`` is the NCIt property code (e.g. ``R105`` for a role, ``A8`` for an
    association); ``relation_label`` is its human-readable name when resolvable.
    """

    relation: str
    relation_label: str | None = None
    target: ConceptRef


class ConceptDetail(StrictBoundaryModel):
    """Full concept detail rendered by the NCIt repository interface.

    Roles (OWL restriction traversal) and associations both appear, plus the
    *incoming* roles (concepts that reference this one) — the empty-roles bug in the
    source platform came from rendering only direct triples and dropping roles.
    """

    code: str
    label: str | None = None
    preferred_name: str | None = None
    definition: str | None = None
    representation_status: RepresentationStatus | None = None
    semantic_types: list[str] = Field(default_factory=list)
    synonyms: list[str] = Field(default_factory=list)
    parents: list[ConceptRef] = Field(default_factory=list)
    children: list[ConceptRef] = Field(default_factory=list)
    roles: list[Relationship] = Field(default_factory=list)
    associations: list[Relationship] = Field(default_factory=list)
    incoming_roles: list[Relationship] = Field(default_factory=list)


class SimilarConcept(StrictBoundaryModel):
    """A concept semantically similar to another (cosine over 768-dim embeddings)."""

    code: str
    label: str | None = None
    score: float


class SearchHit(StrictBoundaryModel):
    """A single row in a search result table."""

    code: str
    label: str | None = None
    semantic_type: str | None = None
    matched_synonym: str | None = None
    representation_status: RepresentationStatus | None = None


class SearchPage(StrictBoundaryModel):
    """A paginated search result."""

    query: str
    total: int
    limit: int
    offset: int
    hits: list[SearchHit] = Field(default_factory=list)


class GraphNode(StrictBoundaryModel):
    """A node in a concept neighborhood graph."""

    code: str
    label: str | None = None
    semantic_type: str | None = None
    representation_status: RepresentationStatus | None = None


class GraphEdge(StrictBoundaryModel):
    """A typed, directed edge in a concept neighborhood graph."""

    source: str
    target: str
    relation: str
    relation_label: str | None = None
    kind: str  # "subClassOf" | "role" | "association" | "cde-concept"


class Neighborhood(StrictBoundaryModel):
    """A concept-centered subgraph for the graph explorer (expand-on-demand).

    ``truncated`` is set when the node cap was reached during expansion, so the client
    can tell a possibly-partial subgraph from a complete one. It errs toward ``True``
    (reaching the cap is reported even in the rare case the graph happened to be
    complete) — a flag whose job is to never claim "complete" when it might not be.
    """

    center: str
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    truncated: bool = False
