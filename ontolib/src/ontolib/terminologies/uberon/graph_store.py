"""Uberon/CL repository reads over the certified QLever index."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ontolib.terminologies.uberon.models import (
    UberonConceptDetail,
    UberonConceptRef,
    UberonEdgeKind,
    UberonGraphEdge,
    UberonGraphNode,
    UberonNeighborhood,
    UberonRelationship,
    UberonSearchHit,
    UberonSearchPage,
    UberonSource,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ontolib.terminologies.sparql_http_client import SparqlHttpClient

_OBO = "http://purl.obolibrary.org/obo/"
_OWL = "http://www.w3.org/2002/07/owl#"
_RDFS = "http://www.w3.org/2000/01/rdf-schema#"
_OIO = "http://www.geneontology.org/formats/oboInOwl#"
_IAO_DEFINITION = f"{_OBO}IAO_0000115"
_PART_OF = f"{_OBO}BFO_0000050"
_PREFIXES = f"PREFIX owl: <{_OWL}>\nPREFIX rdfs: <{_RDFS}>\nPREFIX oio: <{_OIO}>"
_LIST_SEPARATOR = "||"
_DEFAULT_EDGE_LIMIT = 200
_MAX_NEIGHBORHOOD_NODES = 400


def _source_for_iri(iri: str) -> UberonSource:
    if iri.startswith(f"{_OBO}UBERON_"):
        return "uberon"
    if iri.startswith(f"{_OBO}CL_"):
        return "cl"
    raise ValueError("concept IRI is outside the Uberon/CL namespaces")


def _code_for_iri(iri: str) -> str:
    source = _source_for_iri(iri)
    identifier = iri.removeprefix(f"{_OBO}{'UBERON' if source == 'uberon' else 'CL'}_")
    return f"{'UBERON' if source == 'uberon' else 'CL'}:{identifier}"


def _iri_for_code(code: str) -> str:
    try:
        prefix, identifier = code.split(":", 1)
    except ValueError as exc:
        raise ValueError("Uberon/CL code must be a CURIE") from exc
    if prefix not in {"UBERON", "CL"} or not identifier.isdigit():
        raise ValueError("Uberon/CL code must use UBERON:digits or CL:digits")
    return f"{_OBO}{prefix}_{identifier}"


def _source_filter(variable: str, source: UberonSource | None) -> str:
    if source is None:
        return (
            f'FILTER(STRSTARTS(STR({variable}), "{_OBO}UBERON_") || '
            f'STRSTARTS(STR({variable}), "{_OBO}CL_"))'
        )
    prefix = "UBERON" if source == "uberon" else "CL"
    return f'FILTER(STRSTARTS(STR({variable}), "{_OBO}{prefix}_"))'


def _escape_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _ref(iri: str, label: str | None) -> UberonConceptRef:
    return UberonConceptRef(
        code=_code_for_iri(iri), source=_source_for_iri(iri), label=label
    )


class UberonGraphStore:
    """Read-only view of Uberon and its included Cell Ontology classes."""

    def __init__(self, client: SparqlHttpClient) -> None:
        self._client = client
        self._totals: dict[UberonSource | None, int] = {}

    async def get_concept_detail(self, code: str) -> UberonConceptDetail | None:
        iri = _iri_for_code(code)
        rows = await self._client.select(
            f"""{_PREFIXES}
            SELECT ?label ?definition
                   (GROUP_CONCAT(DISTINCT ?synonym;
                     separator="{_LIST_SEPARATOR}") AS ?synonyms)
            WHERE {{
              <{iri}> a owl:Class .
              OPTIONAL {{ <{iri}> rdfs:label ?label }}
              OPTIONAL {{ <{iri}> <{_IAO_DEFINITION}> ?definition }}
              OPTIONAL {{ <{iri}> oio:hasExactSynonym ?synonym }}
            }} GROUP BY ?label ?definition"""
        )
        if not rows:
            return None
        row = rows[0]
        parents = await self._named_neighbors(iri, incoming=False)
        children = await self._named_neighbors(iri, incoming=True)
        relations = await self._restrictions(iri)
        return UberonConceptDetail(
            code=code,
            source=_source_for_iri(iri),
            label=row.get("label"),
            definition=row.get("definition"),
            synonyms=(row.get("synonyms") or "").split(_LIST_SEPARATOR)
            if row.get("synonyms")
            else [],
            parents=parents,
            children=children,
            relations=relations,
        )

    async def _named_neighbors(
        self, iri: str, *, incoming: bool
    ) -> list[UberonConceptRef]:
        pattern = (
            f"?node rdfs:subClassOf <{iri}>"
            if incoming
            else f"<{iri}> rdfs:subClassOf ?node"
        )
        rows = await self._client.select(
            f"""{_PREFIXES}
            SELECT DISTINCT ?node ?label WHERE {{
              {pattern} . FILTER(isIRI(?node))
              {_source_filter("?node", None)}
              OPTIONAL {{ ?node rdfs:label ?label }}
            }} ORDER BY ?node ?label LIMIT {_DEFAULT_EDGE_LIMIT}"""
        )
        return [
            _ref(node, row.get("label"))
            for row in rows
            if (node := row.get("node")) is not None
        ]

    async def _restrictions(self, iri: str) -> list[UberonRelationship]:
        rows = await self._client.select(
            f"""{_PREFIXES}
            SELECT DISTINCT ?rel ?rellabel ?target ?tlabel WHERE {{
              <{iri}> rdfs:subClassOf ?restriction .
              ?restriction a owl:Restriction ; owl:onProperty ?rel ;
                owl:someValuesFrom ?target .
              {_source_filter("?target", None)}
              OPTIONAL {{ ?rel rdfs:label ?rellabel }}
              OPTIONAL {{ ?target rdfs:label ?tlabel }}
            }} ORDER BY ?rel ?target ?rellabel ?tlabel LIMIT {_DEFAULT_EDGE_LIMIT}"""
        )
        relationships: list[UberonRelationship] = []
        for row in rows:
            relation = row.get("rel")
            target = row.get("target")
            if relation is None or target is None:
                continue
            kind: UberonEdgeKind = (
                "part_of" if relation == _PART_OF else "other-restriction"
            )
            relationships.append(
                UberonRelationship(
                    relation=relation.removeprefix(_OBO),
                    relation_label=row.get("rellabel"),
                    kind=kind,
                    target=_ref(target, row.get("tlabel")),
                )
            )
        return relationships

    async def list_concepts(
        self,
        *,
        source: UberonSource | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> UberonSearchPage:
        source_filter = _source_filter("?concept", source)
        rows = await self._client.select(
            f"""{_PREFIXES}
            SELECT ?concept ?label WHERE {{
              ?concept a owl:Class ; rdfs:label ?label . {source_filter}
            }} ORDER BY ?concept ?label LIMIT {limit} OFFSET {offset}"""
        )
        if source not in self._totals:
            count_rows = await self._client.select(
                f"""{_PREFIXES}
                SELECT (COUNT(DISTINCT ?concept) AS ?count) WHERE {{
                  ?concept a owl:Class ; rdfs:label ?label . {source_filter}
                }}"""
            )
            value = count_rows[0].get("count") if count_rows else None
            self._totals[source] = int(value) if value is not None else 0
        return UberonSearchPage(
            query="",
            total=self._totals[source],
            limit=limit,
            offset=offset,
            hits=self._hits(rows),
        )

    async def search(
        self,
        query_text: str,
        *,
        source: UberonSource | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> UberonSearchPage:
        term = _escape_literal(query_text)
        source_filter = _source_filter("?concept", source)
        where = f"""
          ?concept a owl:Class ; rdfs:label ?label . {source_filter}
          OPTIONAL {{ ?concept oio:hasExactSynonym ?synonym .
            FILTER(CONTAINS(LCASE(?synonym), LCASE("{term}"))) }}
          FILTER(CONTAINS(LCASE(?label), LCASE("{term}")) || BOUND(?synonym))
        """
        rows = await self._client.select(
            f"""{_PREFIXES}
            SELECT ?concept ?label (SAMPLE(?synonym) AS ?matched) WHERE {{
              {where}
            }} GROUP BY ?concept ?label ORDER BY ?concept
            LIMIT {limit} OFFSET {offset}"""
        )
        count_rows = await self._client.select(
            f"{_PREFIXES} SELECT (COUNT(DISTINCT ?concept) AS ?count) WHERE {{{where}}}"
        )
        value = count_rows[0].get("count") if count_rows else None
        return UberonSearchPage(
            query=query_text,
            total=int(value) if value is not None else 0,
            limit=limit,
            offset=offset,
            hits=self._hits(rows),
        )

    async def search_records(
        self, *, limit: int, offset: int
    ) -> list[dict[str, str | None]]:
        """Return a deterministic page for rebuilding the PostgreSQL FTS cache."""
        rows = await self._client.select(
            f"""{_PREFIXES}
            SELECT ?concept ?label
              (GROUP_CONCAT(DISTINCT ?synonym;
               separator="{_LIST_SEPARATOR}") AS ?synonyms)
            WHERE {{
              ?concept a owl:Class ; rdfs:label ?label .
              {_source_filter("?concept", None)}
              OPTIONAL {{ ?concept oio:hasExactSynonym ?synonym }}
            }} GROUP BY ?concept ?label ORDER BY ?concept
            LIMIT {limit} OFFSET {offset}"""
        )
        return [
            {
                "code": _code_for_iri(iri),
                "source": _source_for_iri(iri),
                "label": row.get("label"),
                "synonyms": row.get("synonyms") or "",
            }
            for row in rows
            if (iri := row.get("concept")) is not None
        ]

    @staticmethod
    def _hits(rows: Sequence[Mapping[str, str | None]]) -> list[UberonSearchHit]:
        return [
            UberonSearchHit(
                code=_code_for_iri(iri),
                source=_source_for_iri(iri),
                label=row.get("label"),
                matched_synonym=row.get("matched"),
            )
            for row in rows
            if (iri := row.get("concept")) is not None
        ]

    async def get_neighborhood(
        self, code: str, *, depth: int = 1, node_limit: int = _MAX_NEIGHBORHOOD_NODES
    ) -> UberonNeighborhood:
        if not 1 <= node_limit <= _MAX_NEIGHBORHOOD_NODES:
            raise ValueError("node_limit is outside the supported range")
        center = await self.get_concept_detail(code)
        if center is None:
            return UberonNeighborhood(center=code)
        state = _NeighborhoodState(center=center, node_limit=node_limit)
        for _hop in range(depth):
            await self._expand_neighborhood_hop(state)
            if not state.frontier or state.at_limit:
                break
        return UberonNeighborhood(
            center=code,
            nodes=sorted(state.nodes.values(), key=lambda item: item.code),
            edges=sorted(
                state.edges.values(),
                key=lambda item: (item.source, item.target, item.relation, item.kind),
            ),
            truncated=state.truncated,
        )

    async def _expand_neighborhood_hop(self, state: _NeighborhoodState) -> None:
        next_frontier: list[str] = []
        for current in state.frontier:
            detail = await self._detail_for_expansion(current, state)
            if detail is None:
                continue
            state.add_detail(detail)
            next_frontier.extend(state.unexpanded_neighbors(detail))
            if state.at_limit:
                state.truncated = True
                break
        state.frontier = next_frontier

    async def _detail_for_expansion(
        self, current: str, state: _NeighborhoodState
    ) -> UberonConceptDetail | None:
        if current in state.expanded:
            return None
        detail = state.details.get(current) or await self.get_concept_detail(current)
        if detail is not None:
            state.expanded.add(current)
        return detail

    @staticmethod
    def _edge_candidates(
        detail: UberonConceptDetail,
    ) -> list[tuple[UberonConceptRef, UberonGraphEdge]]:
        center = detail.code
        parents = [
            (
                parent,
                UberonGraphEdge(
                    source=center,
                    target=parent.code,
                    relation="subClassOf",
                    kind="subClassOf",
                ),
            )
            for parent in detail.parents
        ]
        children = [
            (
                child,
                UberonGraphEdge(
                    source=child.code,
                    target=center,
                    relation="subClassOf",
                    kind="subClassOf",
                ),
            )
            for child in detail.children
        ]
        restrictions = [
            (
                relation.target,
                UberonGraphEdge(
                    source=center,
                    target=relation.target.code,
                    relation=relation.relation,
                    relation_label=relation.relation_label,
                    kind=relation.kind,
                ),
            )
            for relation in detail.relations
        ]
        return sorted(
            [*parents, *children, *restrictions], key=lambda item: item[0].code
        )


class _NeighborhoodState:
    def __init__(self, *, center: UberonConceptDetail, node_limit: int) -> None:
        self.node_limit = node_limit
        self.nodes: dict[str, UberonGraphNode] = {}
        self.edges: dict[tuple[str, str, str, str], UberonGraphEdge] = {}
        self.frontier = [center.code]
        self.expanded: set[str] = set()
        self.details = {center.code: center}
        self.truncated = False

    @property
    def at_limit(self) -> bool:
        return len(self.nodes) >= self.node_limit

    def unexpanded_neighbors(self, detail: UberonConceptDetail) -> list[str]:
        codes = [ref.code for ref in (*detail.parents, *detail.children)]
        codes.extend(relation.target.code for relation in detail.relations)
        return [code for code in codes if code not in self.expanded]

    def add_detail(self, detail: UberonConceptDetail) -> None:
        dropped = self._add_node(
            UberonConceptRef(code=detail.code, source=detail.source, label=detail.label)
        )
        for ref, edge in UberonGraphStore._edge_candidates(detail):
            dropped = self._add_node(ref) or dropped
            if edge.source in self.nodes and edge.target in self.nodes:
                self.edges.setdefault(
                    (edge.source, edge.target, edge.relation, edge.kind), edge
                )
        self.truncated = dropped or self.truncated

    def _add_node(self, ref: UberonConceptRef) -> bool:
        if ref.code in self.nodes:
            return False
        if self.at_limit:
            return True
        self.nodes[ref.code] = UberonGraphNode.model_validate(ref.model_dump())
        return False
