"""Read models for the combined certified Uberon/Cell Ontology repository."""

import re
from typing import Literal

from pydantic import Field, model_validator

from ontolib.common.boundary_models import StrictFrozenBoundaryModel

UberonSource = Literal["uberon", "cl"]
UberonEdgeKind = Literal["subClassOf", "part_of", "other-restriction"]
_CURIE = re.compile(r"(UBERON|CL):[0-9]+")
_CANONICAL_EDGE_KINDS: dict[str, UberonEdgeKind] = {
    "subClassOf": "subClassOf",
    "BFO_0000050": "part_of",
}


def _required_edge_kind(relation: str) -> UberonEdgeKind:
    return _CANONICAL_EDGE_KINDS.get(relation, "other-restriction")


def _source_for_code(code: str) -> UberonSource:
    match = _CURIE.fullmatch(code)
    if match is None:
        raise ValueError("code must use UBERON:digits or CL:digits")
    return "uberon" if match.group(1) == "UBERON" else "cl"


def _require_closed_neighborhood(
    center: str, nodes: list[UberonGraphNode], edges: list[UberonGraphEdge]
) -> None:
    _source_for_code(center)
    node_codes = [node.code for node in nodes]
    if len(node_codes) != len(set(node_codes)):
        raise ValueError("neighborhood node codes must be unique")
    if center not in node_codes:
        raise ValueError("neighborhood center must be present in nodes")
    represented = set(node_codes)
    endpoints = {endpoint for edge in edges for endpoint in (edge.source, edge.target)}
    if not endpoints.issubset(represented):
        raise ValueError("neighborhood edges must have represented endpoints")


class _ReadModel(StrictFrozenBoundaryModel):
    pass


class _SourcedConcept(_ReadModel):
    code: str
    source: UberonSource

    @model_validator(mode="after")
    def _source_matches_code(self) -> _SourcedConcept:
        if _source_for_code(self.code) != self.source:
            raise ValueError("source does not match the concept CURIE namespace")
        return self


class UberonConceptRef(_SourcedConcept):
    label: str | None = None


class UberonRelationship(_ReadModel):
    relation: str
    relation_label: str | None = None
    kind: UberonEdgeKind
    target: UberonConceptRef

    @model_validator(mode="after")
    def _kind_matches_relation(self) -> UberonRelationship:
        if self.kind != _required_edge_kind(self.relation):
            raise ValueError("relation requires its canonical edge kind")
        return self


class UberonConceptDetail(_SourcedConcept):
    label: str | None = None
    definition: str | None = None
    synonyms: list[str] = Field(default_factory=list)
    xrefs: list[str] = Field(default_factory=list)
    parents: list[UberonConceptRef] = Field(default_factory=list)
    children: list[UberonConceptRef] = Field(default_factory=list)
    relations: list[UberonRelationship] = Field(default_factory=list)
    truncated: bool = False


class UberonSearchHit(_SourcedConcept):
    label: str | None = None
    matched_synonym: str | None = None


class UberonSearchPage(_ReadModel):
    query: str
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    hits: list[UberonSearchHit] = Field(default_factory=list)


class UberonGraphNode(_SourcedConcept):
    label: str | None = None


class UberonGraphEdge(_ReadModel):
    source: str
    target: str
    relation: str
    relation_label: str | None = None
    kind: UberonEdgeKind

    @model_validator(mode="after")
    def _valid_endpoints_and_kind(self) -> UberonGraphEdge:
        _source_for_code(self.source)
        _source_for_code(self.target)
        required = _required_edge_kind(self.relation)
        if self.kind != required:
            raise ValueError("canonical relations require their canonical edge kind")
        return self


class UberonNeighborhood(_ReadModel):
    center: str
    nodes: list[UberonGraphNode] = Field(default_factory=list)
    edges: list[UberonGraphEdge] = Field(default_factory=list)
    truncated: bool = False

    @model_validator(mode="after")
    def _closed_graph(self) -> UberonNeighborhood:
        _require_closed_neighborhood(self.center, self.nodes, self.edges)
        return self
