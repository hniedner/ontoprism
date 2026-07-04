"""NCIt repository read model over an Oxigraph SPARQL endpoint.

Assembles concept detail (metadata + hierarchy + roles + associations + incoming
roles), search, and expand-on-demand neighborhoods. Roles are recovered by OWL
restriction traversal (see :mod:`ontolib.terminologies.ncit.role_queries`); rendering
them is the point — the source platform's empty-roles bug came from querying only
direct triples.
"""

from __future__ import annotations

from ontolib.terminologies.namespaces import NCIT_NS, OWL_NS, RDFS_NS
from ontolib.terminologies.ncit import property_codes as pc
from ontolib.terminologies.ncit.models import (
    ConceptDetail,
    ConceptRef,
    GraphEdge,
    GraphNode,
    Neighborhood,
    Relationship,
    SearchHit,
    SearchPage,
)
from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient, safe_iri

_PREFIXES = (
    f"PREFIX rdfs: <{RDFS_NS}>\nPREFIX owl: <{OWL_NS}>\nPREFIX ncit: <{NCIT_NS}>"
)
_LIST_SEP = "||"
_DEFAULT_EDGE_LIMIT = 200
# Upper bound on nodes returned by a multi-hop neighborhood expansion, so a deep
# request cannot pull an unbounded closure out of the store.
_MAX_NEIGHBORHOOD_NODES = 400


def _code_of(uri: str) -> str:
    """Return the trailing NCIt code of an IRI (``…#C3262`` -> ``C3262``)."""
    return uri.rsplit("#", 1)[-1] if "#" in uri else uri.rsplit("/", 1)[-1]


def _escape_literal(text: str) -> str:
    """Escape a user string for safe embedding in a SPARQL double-quoted literal."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _ref(uri: str | None, label: str | None) -> ConceptRef | None:
    """Build a ConceptRef from a possibly-unbound node URI."""
    return ConceptRef(code=_code_of(uri), label=label) if uri else None


def _rel(
    rel_uri: str | None,
    rel_label: str | None,
    target_uri: str | None,
    target_label: str | None,
) -> Relationship | None:
    """Build a Relationship, or None if the relation or target is unbound."""
    if not (rel_uri and target_uri):
        return None
    return Relationship(
        relation=_code_of(rel_uri),
        relation_label=rel_label,
        target=ConceptRef(code=_code_of(target_uri), label=target_label),
    )


class NcitGraphStore:
    """Read-only NCIt repository backed by an Oxigraph SPARQL endpoint."""

    def __init__(self, client: OxigraphHttpClient, *, namespace: str = NCIT_NS) -> None:
        """Wrap a SPARQL *client*; concept IRIs are ``{namespace}{code}``."""
        self._client = client
        self._ns = namespace
        self._total_concepts: int | None = None

    # ------------------------------------------------------------------ detail

    async def get_concept_detail(self, code: str) -> ConceptDetail | None:
        """Return full detail for *code*, or ``None`` if the concept does not exist."""
        uri = safe_iri(code, self._ns)
        meta = await self._client.select(self._metadata_query(uri))
        if not meta:
            return None
        row = meta[0]
        return ConceptDetail(
            code=code,
            label=row.get("label"),
            preferred_name=row.get("pref"),
            definition=row.get("def"),
            semantic_types=_split_list(row.get("semtypes")),
            synonyms=_split_list(row.get("synonyms")),
            parents=await self._named_neighbors(uri, incoming=False),
            children=await self._named_neighbors(uri, incoming=True),
            roles=await self._roles(uri),
            associations=await self._associations(uri),
            incoming_roles=await self._incoming_roles(uri),
        )

    def _metadata_query(self, uri: str) -> str:
        return f"""{_PREFIXES}
        SELECT ?label ?pref ?def
               (GROUP_CONCAT(DISTINCT ?semtype; separator="{_LIST_SEP}") AS ?semtypes)
               (GROUP_CONCAT(DISTINCT ?syn; separator="{_LIST_SEP}") AS ?synonyms)
        WHERE {{
            <{uri}> a owl:Class .
            OPTIONAL {{ <{uri}> rdfs:label ?label }}
            OPTIONAL {{ <{uri}> ncit:{pc.PREFERRED_NAME} ?pref }}
            OPTIONAL {{ <{uri}> ncit:{pc.DEFINITION} ?def }}
            OPTIONAL {{ <{uri}> ncit:{pc.SEMANTIC_TYPE} ?semtype }}
            OPTIONAL {{ <{uri}> ncit:{pc.FULL_SYNONYM} ?syn }}
        }}
        GROUP BY ?label ?pref ?def
        """

    async def _named_neighbors(self, uri: str, *, incoming: bool) -> list[ConceptRef]:
        """Named parents (``incoming=False``) or children (``incoming=True``)."""
        pattern = (
            f"?node rdfs:subClassOf <{uri}>"
            if incoming
            else f"<{uri}> rdfs:subClassOf ?node"
        )
        query = f"""{_PREFIXES}
        SELECT DISTINCT ?node ?label WHERE {{
            {pattern} .
            FILTER(isIRI(?node) && STRSTARTS(STR(?node), "{self._ns}"))
            OPTIONAL {{ ?node rdfs:label ?label }}
        }} LIMIT {_DEFAULT_EDGE_LIMIT}
        """
        rows = await self._client.select(query)
        refs = (_ref(r.get("node"), r.get("label")) for r in rows)
        return [ref for ref in refs if ref is not None]

    async def _roles(self, uri: str) -> list[Relationship]:
        query = f"""{_PREFIXES}
        SELECT ?rel ?rellabel ?target ?tlabel WHERE {{
            <{uri}> rdfs:subClassOf ?r .
            ?r a owl:Restriction ;
               owl:onProperty ?rel ;
               owl:someValuesFrom ?target .
            FILTER(STRSTARTS(STR(?target), "{self._ns}"))
            OPTIONAL {{ ?rel rdfs:label ?rellabel }}
            OPTIONAL {{ ?target rdfs:label ?tlabel }}
        }} LIMIT {_DEFAULT_EDGE_LIMIT}
        """
        return self._as_relationships(await self._client.select(query))

    async def _associations(self, uri: str) -> list[Relationship]:
        query = f"""{_PREFIXES}
        SELECT ?rel ?rellabel ?target ?tlabel WHERE {{
            <{uri}> ?rel ?target .
            FILTER(isIRI(?target) && STRSTARTS(STR(?target), "{self._ns}"))
            FILTER(?rel != rdfs:subClassOf)
            OPTIONAL {{ ?rel rdfs:label ?rellabel }}
            OPTIONAL {{ ?target rdfs:label ?tlabel }}
        }} LIMIT {_DEFAULT_EDGE_LIMIT}
        """
        return self._as_relationships(await self._client.select(query))

    async def _incoming_roles(self, uri: str) -> list[Relationship]:
        query = f"""{_PREFIXES}
        SELECT ?rel ?rellabel ?src ?slabel WHERE {{
            ?r a owl:Restriction ;
               owl:onProperty ?rel ;
               owl:someValuesFrom <{uri}> .
            ?src rdfs:subClassOf ?r .
            FILTER(STRSTARTS(STR(?src), "{self._ns}"))
            OPTIONAL {{ ?rel rdfs:label ?rellabel }}
            OPTIONAL {{ ?src rdfs:label ?slabel }}
        }} LIMIT {_DEFAULT_EDGE_LIMIT}
        """
        rows = await self._client.select(query)
        rels = (
            _rel(r.get("rel"), r.get("rellabel"), r.get("src"), r.get("slabel"))
            for r in rows
        )
        return [rel for rel in rels if rel is not None]

    def _as_relationships(
        self, rows: list[dict[str, str | None]]
    ) -> list[Relationship]:
        rels = (
            _rel(r.get("rel"), r.get("rellabel"), r.get("target"), r.get("tlabel"))
            for r in rows
        )
        return [rel for rel in rels if rel is not None]

    # ------------------------------------------------------------------ search

    async def search(
        self, query_text: str, *, limit: int = 25, offset: int = 0
    ) -> SearchPage:
        """Case-insensitive search over preferred label and synonyms."""
        term = _escape_literal(query_text)
        where = f"""
            ?concept a owl:Class ; rdfs:label ?label .
            OPTIONAL {{ ?concept ncit:{pc.SEMANTIC_TYPE} ?semtype }}
            OPTIONAL {{
                ?concept ncit:{pc.FULL_SYNONYM} ?syn .
                FILTER(CONTAINS(LCASE(?syn), LCASE("{term}")))
            }}
            FILTER(CONTAINS(LCASE(?label), LCASE("{term}")) || BOUND(?syn))
        """
        # GROUP BY concept so a concept with several matching synonyms / semantic
        # types yields exactly one result row (not one row per synonym).
        rows = await self._client.select(
            f"""{_PREFIXES}
            SELECT ?concept ?label
                   (SAMPLE(?semtype) AS ?semtype) (SAMPLE(?syn) AS ?syn)
            WHERE {{{where}}}
            GROUP BY ?concept ?label
            ORDER BY ?label LIMIT {limit} OFFSET {offset}
            """
        )
        count_rows = await self._client.select(
            f"{_PREFIXES}\n"
            f"SELECT (COUNT(DISTINCT ?concept) AS ?count) WHERE {{{where}}}"
        )
        count_val = count_rows[0].get("count") if count_rows else None
        total = int(count_val) if count_val is not None else 0
        hits = [
            SearchHit(
                code=_code_of(concept),
                label=r.get("label"),
                semantic_type=r.get("semtype"),
                matched_synonym=r.get("syn"),
            )
            for r in rows
            if (concept := r.get("concept")) is not None
        ]
        return SearchPage(
            query=query_text, total=total, limit=limit, offset=offset, hits=hits
        )

    async def labels_for(self, codes: list[str]) -> dict[str, str]:
        """Return a ``{code: label}`` map for the given codes (one query)."""
        if not codes:
            return {}
        values = " ".join(f"<{safe_iri(c, self._ns)}>" for c in codes)
        query = f"""{_PREFIXES}
        SELECT ?c ?label WHERE {{
            VALUES ?c {{ {values} }}
            ?c rdfs:label ?label .
        }}
        """
        rows = await self._client.select(query)
        return {
            _code_of(c): label
            for r in rows
            if (c := r.get("c")) and (label := r.get("label"))
        }

    # -------------------------------------------------------------------- browse

    async def list_concepts(self, *, limit: int = 25, offset: int = 0) -> SearchPage:
        """List all named concepts in natural (code) order — the no-search browse mode.

        The total class count is expensive to compute over the full store, so it is
        memoized after the first call (the concept universe is static between reloads).
        """
        rows = await self._client.select(
            f"""{_PREFIXES}
            SELECT ?concept ?label (SAMPLE(?semtype) AS ?semtype)
            WHERE {{
                ?concept a owl:Class ; rdfs:label ?label .
                OPTIONAL {{ ?concept ncit:{pc.SEMANTIC_TYPE} ?semtype }}
                FILTER(STRSTARTS(STR(?concept), "{self._ns}"))
            }}
            GROUP BY ?concept ?label
            ORDER BY ?concept LIMIT {limit} OFFSET {offset}
            """
        )
        if self._total_concepts is None:
            count_rows = await self._client.select(
                f"""{_PREFIXES}
                SELECT (COUNT(DISTINCT ?concept) AS ?count) WHERE {{
                    ?concept a owl:Class ; rdfs:label ?label .
                    FILTER(STRSTARTS(STR(?concept), "{self._ns}"))
                }}
                """
            )
            count_val = count_rows[0].get("count") if count_rows else None
            self._total_concepts = int(count_val) if count_val is not None else 0
        hits = [
            SearchHit(
                code=_code_of(concept),
                label=r.get("label"),
                semantic_type=r.get("semtype"),
                matched_synonym=None,
            )
            for r in rows
            if (concept := r.get("concept")) is not None
        ]
        return SearchPage(
            query="", total=self._total_concepts, limit=limit, offset=offset, hits=hits
        )

    # ------------------------------------------------------------- neighborhood

    async def get_neighborhood(self, code: str, *, depth: int = 1) -> Neighborhood:
        """Return a concept-centered subgraph (subClassOf + roles + associations).

        ``depth`` hops are expanded breadth-first from *code*: every node discovered at
        hop *n* is itself expanded at hop *n+1*, up to ``depth``. Growth is bounded by
        ``_MAX_NEIGHBORHOOD_NODES`` so a deep request cannot pull a huge closure.
        """
        center_detail = await self.get_concept_detail(code)
        if center_detail is None:
            return Neighborhood(center=code)

        nodes: dict[str, GraphNode] = {}
        edges: dict[tuple[str, str, str, str], GraphEdge] = {}
        expanded: set[str] = set()
        frontier = [code]
        details: dict[str, ConceptDetail] = {code: center_detail}

        for _hop in range(depth):
            frontier = await self._expand_hop(frontier, expanded, nodes, edges, details)
            if not frontier or len(nodes) >= _MAX_NEIGHBORHOOD_NODES:
                break

        # The center always carries its semantic type even if its node was seeded
        # label-only by an earlier neighbor reference.
        nodes[code] = GraphNode(
            code=code,
            label=center_detail.label,
            semantic_type=_first(center_detail.semantic_types),
        )
        return Neighborhood(
            center=code,
            nodes=list(nodes.values()),
            edges=list(edges.values()),
        )

    async def _expand_hop(
        self,
        frontier: list[str],
        expanded: set[str],
        nodes: dict[str, GraphNode],
        edges: dict[tuple[str, str, str, str], GraphEdge],
        details: dict[str, ConceptDetail],
    ) -> list[str]:
        """Expand one BFS hop: add each frontier node's edges, return the next hop."""
        next_frontier: list[str] = []
        for current in frontier:
            detail = await self._detail_to_expand(current, expanded, details)
            if detail is None:
                continue
            self._add_edges(current, detail, nodes, edges)
            if len(nodes) >= _MAX_NEIGHBORHOOD_NODES:
                break
            next_frontier.extend(
                n for n in self._neighbor_codes(detail) if n not in expanded
            )
        return next_frontier

    async def _detail_to_expand(
        self,
        current: str,
        expanded: set[str],
        details: dict[str, ConceptDetail],
    ) -> ConceptDetail | None:
        """Return *current*'s detail to expand (None if already seen or missing)."""
        if current in expanded:
            return None
        detail = details.get(current) or await self.get_concept_detail(current)
        if detail is None:
            return None
        expanded.add(current)
        return detail

    @staticmethod
    def _neighbor_codes(detail: ConceptDetail) -> list[str]:
        refs = [*detail.parents, *detail.children]
        rels = [*detail.roles, *detail.associations]
        return [r.code for r in refs] + [rel.target.code for rel in rels]

    @staticmethod
    def _add_edges(
        code: str,
        detail: ConceptDetail,
        nodes: dict[str, GraphNode],
        edges: dict[tuple[str, str, str, str], GraphEdge],
    ) -> None:
        def add_node(node_code: str, label: str | None) -> None:
            nodes.setdefault(node_code, GraphNode(code=node_code, label=label))

        def add_edge(edge: GraphEdge) -> None:
            edges.setdefault((edge.source, edge.target, edge.relation, edge.kind), edge)

        add_node(code, detail.label)
        for parent in detail.parents:
            add_node(parent.code, parent.label)
            add_edge(
                GraphEdge(
                    source=code,
                    target=parent.code,
                    relation="subClassOf",
                    kind="subClassOf",
                )
            )
        for child in detail.children:
            add_node(child.code, child.label)
            add_edge(
                GraphEdge(
                    source=child.code,
                    target=code,
                    relation="subClassOf",
                    kind="subClassOf",
                )
            )
        for rel in detail.roles:
            add_node(rel.target.code, rel.target.label)
            add_edge(
                GraphEdge(
                    source=code,
                    target=rel.target.code,
                    relation=rel.relation,
                    relation_label=rel.relation_label,
                    kind="role",
                )
            )
        for rel in detail.associations:
            add_node(rel.target.code, rel.target.label)
            add_edge(
                GraphEdge(
                    source=code,
                    target=rel.target.code,
                    relation=rel.relation,
                    relation_label=rel.relation_label,
                    kind="association",
                )
            )


def _split_list(value: str | None) -> list[str]:
    return [item for item in value.split(_LIST_SEP) if item] if value else []


def _first(items: list[str]) -> str | None:
    return items[0] if items else None
