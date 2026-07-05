"""Read models for the NCIt repository (pydantic, serialized directly by the API)."""

from pydantic import BaseModel


class ConceptRef(BaseModel):
    """A lightweight reference to a concept: its code and best-available label."""

    code: str
    label: str | None = None


class Relationship(BaseModel):
    """A typed edge from a concept to a target concept.

    ``relation`` is the NCIt property code (e.g. ``R105`` for a role, ``A8`` for an
    association); ``relation_label`` is its human-readable name when resolvable.
    """

    relation: str
    relation_label: str | None = None
    target: ConceptRef


class ConceptDetail(BaseModel):
    """Full concept detail rendered by the NCIt repository interface.

    Roles (OWL restriction traversal) and associations both appear, plus the
    *incoming* roles (concepts that reference this one) — the empty-roles bug in the
    source platform came from rendering only direct triples and dropping roles.
    """

    code: str
    label: str | None = None
    preferred_name: str | None = None
    definition: str | None = None
    semantic_types: list[str] = []
    synonyms: list[str] = []
    parents: list[ConceptRef] = []
    children: list[ConceptRef] = []
    roles: list[Relationship] = []
    associations: list[Relationship] = []
    incoming_roles: list[Relationship] = []


class SimilarConcept(BaseModel):
    """A concept semantically similar to another (cosine over 768-dim embeddings)."""

    code: str
    label: str | None = None
    score: float


class SearchHit(BaseModel):
    """A single row in a search result table."""

    code: str
    label: str | None = None
    semantic_type: str | None = None
    matched_synonym: str | None = None


class SearchPage(BaseModel):
    """A paginated search result."""

    query: str
    total: int
    limit: int
    offset: int
    hits: list[SearchHit] = []


class GraphNode(BaseModel):
    """A node in a concept neighborhood graph."""

    code: str
    label: str | None = None
    semantic_type: str | None = None


class GraphEdge(BaseModel):
    """A typed, directed edge in a concept neighborhood graph."""

    source: str
    target: str
    relation: str
    relation_label: str | None = None
    kind: str  # "subClassOf" | "role" | "association"


class Neighborhood(BaseModel):
    """A concept-centered subgraph for the graph explorer (expand-on-demand).

    ``truncated`` is set when the node cap was hit and some neighbors were dropped, so
    the client can tell a partial subgraph from a complete one.
    """

    center: str
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    truncated: bool = False
