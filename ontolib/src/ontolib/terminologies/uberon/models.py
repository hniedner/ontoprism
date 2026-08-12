"""Read models for the combined certified Uberon/Cell Ontology repository."""

from typing import Literal

from pydantic import BaseModel

UberonSource = Literal["uberon", "cl"]
UberonEdgeKind = Literal["subClassOf", "part_of", "other-restriction"]


class UberonConceptRef(BaseModel):
    code: str
    source: UberonSource
    label: str | None = None


class UberonRelationship(BaseModel):
    relation: str
    relation_label: str | None = None
    kind: UberonEdgeKind
    target: UberonConceptRef


class UberonConceptDetail(BaseModel):
    code: str
    source: UberonSource
    label: str | None = None
    definition: str | None = None
    synonyms: list[str] = []
    parents: list[UberonConceptRef] = []
    children: list[UberonConceptRef] = []
    relations: list[UberonRelationship] = []


class UberonSearchHit(BaseModel):
    code: str
    source: UberonSource
    label: str | None = None
    matched_synonym: str | None = None


class UberonSearchPage(BaseModel):
    query: str
    total: int
    limit: int
    offset: int
    hits: list[UberonSearchHit] = []


class UberonGraphNode(BaseModel):
    code: str
    source: UberonSource
    label: str | None = None


class UberonGraphEdge(BaseModel):
    source: str
    target: str
    relation: str
    relation_label: str | None = None
    kind: UberonEdgeKind


class UberonNeighborhood(BaseModel):
    center: str
    nodes: list[UberonGraphNode] = []
    edges: list[UberonGraphEdge] = []
    truncated: bool = False
