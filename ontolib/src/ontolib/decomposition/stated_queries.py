"""SPARQL builders that read the **stated** NCIt named graph (design §3, §2).

Extraction must run off the stated (asserted) OWL, never the inferred default graph,
to avoid ancestor-closure bleed and the ``Excludes_*`` negative axioms (assessment §4).
These builders reuse the restriction-traversal pattern from ``role_queries.py`` wrapped
in a ``GRAPH <STATED_GRAPH_IRI>`` clause, and reuse ``safe_iri`` for injection safety.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

from ontolib.decomposition.complete_definition import read_complete_definition
from ontolib.decomposition.extract import (
    PartOfPair,
    part_of_expansions_from_rows,
)
from ontolib.decomposition.models import (
    CompleteDefinition,
    GenusDefinitionFact,
    ResolvedR82Path,
    ResolvedR82PathEdge,
    RestrictionDefinitionFact,
    RoleRestriction,
)
from ontolib.decomposition.r101_conservation import r82_fact_identity
from ontolib.terminologies.namespaces import NCIT_NS, OWL_NS, RDF_NS, RDFS_NS
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI
from ontolib.terminologies.ncit.property_codes import SEMANTIC_TYPE
from ontolib.terminologies.sparql_transport import safe_iri

if TYPE_CHECKING:
    from collections.abc import Awaitable, Collection, Iterable, Mapping, Sequence


_PREFIXES = f"""
        PREFIX rdfs: <{RDFS_NS}>
        PREFIX rdf: <{RDF_NS}>
        PREFIX owl: <{OWL_NS}>
"""

# Maximum rdf:rest hops inside an owl:intersectionOf list. Lists are 2-3 members;
# 6 leaves generous margin for deeply nested lists without sending unbounded rdf:rest*
# queries that the engine may plan poorly on a large stated graph.
_MAX_INTERSECTION_HOPS = 6

# The 16 x 16 request was measured against a disposable clone of the full store:
# 43 KB, 3.3 ms, with both endpoints bound before traversal in the measured plan.
_PART_OF_QUERY_CODE_LIMIT = 16
_PART_OF_EXPANSION_ROW_LIMIT = 256
_PART_OF_MAX_R82_HOPS = 8
_PART_OF_MAX_SUPERCLASS_HOPS = 20
_PART_OF_MAX_EXPANDED_CODES = 256
_PART_OF_CANDIDATE_PREFLIGHT_LIMIT = 64
_PART_OF_MAX_REQUESTS = 64
_PART_OF_MAX_TOTAL_ROWS = 4096
_PART_OF_MAX_QUERY_BYTES = 65_536
_NCIT_CONCEPT_CODE = re.compile(r"C[0-9]+")

# A semantic type is a plain-text SPARQL literal (not an IRI, so ``safe_iri`` does not
# apply): reject anything that could close the literal or inject a graph pattern.
_SAFE_LITERAL = re.compile(r'^[^"\\\n{}]+$')


class SelectRows(Protocol):
    def __call__(
        self,
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> Awaitable[Sequence[Mapping[str, str | None]]]: ...


class SingleAttemptSelectRows(Protocol):
    """Client surface whose SELECT operation performs one transport attempt."""

    def select_once(
        self,
        query: str,
        *,
        required_variables: Collection[str] = (),
    ) -> Awaitable[Sequence[Mapping[str, str | None]]]: ...


@dataclass(frozen=True, slots=True)
class _PartOfNodeExpansion:
    parents: frozenset[str]
    wholes: frozenset[str]
    whole_evidence: tuple[tuple[str, str], ...] = ()


def _frontier_nodes(frontiers: Mapping[str, set[str]]) -> set[str]:
    return {node for frontier in frontiers.values() for node in frontier}


def _next_superclass_frontiers(
    cache: Mapping[str, _PartOfNodeExpansion],
    frontiers: Mapping[str, set[str]],
    visited: Mapping[str, set[str]],
    wholes: Mapping[str, set[str]],
) -> dict[str, set[str]]:
    next_frontiers: dict[str, set[str]] = {}
    for root, frontier in frontiers.items():
        parents: set[str] = set()
        for node in frontier:
            wholes[root].update(cache[node].wholes)
            parents.update(cache[node].parents)
        parents.difference_update(visited[root])
        visited[root].update(parents)
        next_frontiers[root] = parents
    return next_frontiers


def _next_part_of_frontiers(
    frontiers: Mapping[str, set[str]],
    outgoing: Mapping[str, set[str]],
    reached: Mapping[str, set[str]],
) -> dict[str, set[str]]:
    next_frontiers: dict[str, set[str]] = {}
    for origin, frontier in frontiers.items():
        targets: set[str] = set()
        for node in frontier:
            targets.update(outgoing[node])
        targets.difference_update(reached[origin])
        next_frontiers[origin] = targets
    return next_frontiers


def _record_requested_pairs(
    next_frontiers: Mapping[str, set[str]],
    reached: Mapping[str, set[str]],
    requested: frozenset[str],
    pairs: set[PartOfPair],
) -> None:
    for origin, frontier in next_frontiers.items():
        reached[origin].update(frontier)
        for whole in frontier & requested:
            pairs.add(PartOfPair(part=origin, whole=whole))


def _safe_literal(value: str) -> str:
    """Return *value* unchanged, rejecting injection-unsafe literals.

    Raises:
        ValueError: if *value* contains a quote, backslash, newline, or brace.
    """
    if not _SAFE_LITERAL.match(value):
        raise ValueError(f"Unsafe semantic type rejected: {value!r}")
    return value


def _intersection_hop_pattern(concept_uri: str, hop: int) -> str:
    """Return triple patterns for Nth member of each intersectionOf list.

    Uses ONLY individual triple patterns (no property paths) so the store
    binds the subject correctly rather than doing a graph-wide scan.
    """
    if hop == 0:
        return f"""
        <{concept_uri}> owl:equivalentClass ?ec .
        ?ec owl:intersectionOf ?list .
        ?list rdf:first ?member ."""
    # hop=1: ?list rdf:rest ?mid0 . ?mid0 rdf:first ?member
    # hop=2: ?list rdf:rest ?mid0 . ?mid0 rdf:rest ?mid1 . ?mid1 rdf:first ?member
    lines = [
        f"<{concept_uri}> owl:equivalentClass ?ec .",
        "?ec owl:intersectionOf ?list .",
    ]
    for i in range(hop):
        prev = "?list" if i == 0 else f"?mid{i - 1}"
        curr = f"?mid{i}"
        lines.append(f"{prev} rdf:rest {curr} .")
    lines.append(f"?mid{hop - 1} rdf:first ?member .")
    return "\n".join(lines)


def build_genus_walk_members_query(
    concept_code: str,
) -> list[str]:
    """Query(s) collecting ALL intersectionOf members of *concept_code*.

    Returns a list of SPARQL SELECT queries, one per hop depth (``rdf:first``,
    ``rdf:rest/rdf:first``, … ``rdf:rest^N/rdf:first``). Each query returns the
    member at that hop position across ALL ``owl:equivalentClass`` paths.

    Uses individual triple patterns per hop rather than property paths to keep
    the query planner anchored on the subject concept.

    Raises:
        ValueError: if *concept_code* is not injection-safe.
    """
    concept_uri = safe_iri(concept_code, NCIT_NS)
    queries: list[str] = []
    for hop in range(_MAX_INTERSECTION_HOPS):
        patterns = _intersection_hop_pattern(concept_uri, hop)
        queries.append(
            f"""{_PREFIXES}
            SELECT ?member ?type ?role ?target ?roleLabel WHERE {{
                GRAPH <{STATED_GRAPH_IRI}> {{
                    {patterns}
                    OPTIONAL {{ ?member a ?type }}
                    OPTIONAL {{
                        ?member owl:onProperty ?role ; owl:someValuesFrom ?target .
                    }}
                }}
                OPTIONAL {{
                    GRAPH <{STATED_GRAPH_IRI}> {{
                        ?member owl:onProperty ?role .
                    }}
                    ?role rdfs:label ?roleLabel .
                }}
            }}
            """
        )
    return queries


def build_semantic_type_of_query(codes: list[str]) -> str:
    """Batch-resolve ``P106`` (semantic type) for *codes* in the stated graph.

    Projects ``?code`` (the NCIt code) and ``?st`` (the semantic type literal).
    Returns a valid query even for an empty list (matches nothing).

    Raises:
        ValueError: if any code is not injection-safe.
    """
    if not codes:
        return f"{_PREFIXES}SELECT ?code ?st WHERE {{ BIND(false AS ?ok) }}"
    iris = " ".join(f"<{safe_iri(code, NCIT_NS)}>" for code in codes)
    semantic_type_uri = f"{NCIT_NS}{SEMANTIC_TYPE}"
    return f"""{_PREFIXES}
        SELECT ?code ?st WHERE {{
            GRAPH <{STATED_GRAPH_IRI}> {{
                VALUES ?concept {{ {iris} }}
                ?concept <{semantic_type_uri}> ?st .
            }}
            BIND(REPLACE(STR(?concept), ".*#", "") AS ?code)
        }}
    """


def _part_of_codes(codes: Iterable[str]) -> tuple[str, ...]:
    unique_codes = set(codes)
    for code in unique_codes:
        safe_iri(code, NCIT_NS)
    invalid_codes = sorted(
        code for code in unique_codes if _NCIT_CONCEPT_CODE.fullmatch(code) is None
    )
    if invalid_codes:
        raise ValueError(
            f"R82 endpoint is not an NCIt concept code: {invalid_codes[0]!r}"
        )
    return tuple(sorted(unique_codes))


def _part_of_endpoint_iris(codes: Iterable[str]) -> tuple[str, ...]:
    code_list = _part_of_codes(codes)
    if len(code_list) > _PART_OF_QUERY_CODE_LIMIT:
        raise ValueError(
            "R82 query accepts at most 16 codes per endpoint (256 combinations)"
        )
    return tuple(safe_iri(code, NCIT_NS) for code in code_list)


def build_part_of_pairs_query(
    *, part_codes: Iterable[str], whole_codes: Iterable[str]
) -> str:
    """Build one bounded R82 restriction query for two endpoint tiles.

    Every requested part-whole combination is emitted as an IRI tuple so QLever
    binds both endpoints before traversing the part's stated superclass path. A
    request is limited to 16 codes per endpoint (256 combinations); callers handling
    larger sets must tile both dimensions with :func:`build_part_of_pairs_queries`.
    This finds one R82 edge inherited through ``rdfs:subClassOf*``; it does not compute
    an R82-to-R82 transitive closure.

    Raises:
        ValueError: if any code is unsafe or either endpoint exceeds the measured cap.
    """
    part_iris = _part_of_endpoint_iris(part_codes)
    whole_iris = _part_of_endpoint_iris(whole_codes)
    if not part_iris or not whole_iris:
        return f"{_PREFIXES}SELECT ?part ?whole WHERE {{ FILTER(false) }}"
    pairs = " ".join(
        f"(<{part}> <{whole}>)" for part in part_iris for whole in whole_iris
    )
    return f"""{_PREFIXES}
        SELECT DISTINCT ?part ?whole WHERE {{
            VALUES (?part ?whole) {{ {pairs} }}
            GRAPH <{STATED_GRAPH_IRI}> {{
                ?part rdfs:subClassOf* ?ancestor .
                ?ancestor rdfs:subClassOf ?restriction .
                ?restriction a owl:Restriction ;
                    owl:onProperty <{NCIT_NS}R82> ;
                    owl:someValuesFrom ?whole .
            }}
        }}
    """


def build_part_of_pairs_queries(codes: Iterable[str]) -> list[str]:
    """Tile every part-whole combination in *codes* into bounded R82 queries."""
    code_list = sorted(set(codes))
    chunks = [
        code_list[start : start + _PART_OF_QUERY_CODE_LIMIT]
        for start in range(0, len(code_list), _PART_OF_QUERY_CODE_LIMIT)
    ]
    return [
        build_part_of_pairs_query(
            part_codes=part_chunk,
            whole_codes=whole_chunk,
        )
        for part_chunk in chunks
        for whole_chunk in chunks
    ]


def build_part_of_candidate_paths_query(
    pairs: Iterable[tuple[str, str]],
) -> str:
    """Build a direct/inherited one-R82-edge preflight for exact candidate pairs."""
    candidates = tuple(sorted(set(pairs)))
    if len(candidates) > _PART_OF_CANDIDATE_PREFLIGHT_LIMIT:
        raise ValueError("R82 candidate preflight accepts at most 64 pairs")
    if not candidates:
        return (
            f"{_PREFIXES}SELECT ?part ?whole ?assertedPart ?restriction "
            "WHERE { FILTER(false) }"
        )
    values: list[str] = []
    for part, whole in candidates:
        part_iri = safe_iri(part, NCIT_NS)
        whole_iri = safe_iri(whole, NCIT_NS)
        if (
            _NCIT_CONCEPT_CODE.fullmatch(part) is None
            or _NCIT_CONCEPT_CODE.fullmatch(whole) is None
        ):
            raise ValueError("R82 endpoint is not an NCIt concept code")
        values.append(f"(<{part_iri}> <{whole_iri}>)")
    return f"""{_PREFIXES}
        SELECT DISTINCT ?part ?whole ?assertedPart ?restriction WHERE {{
            VALUES (?part ?whole) {{ {" ".join(values)} }}
            GRAPH <{STATED_GRAPH_IRI}> {{
                ?part rdfs:subClassOf* ?assertedPart .
                ?assertedPart rdfs:subClassOf ?restriction .
                ?restriction a owl:Restriction ;
                    owl:onProperty <{NCIT_NS}R82> ;
                    owl:someValuesFrom ?whole .
            }}
        }}
        ORDER BY ?part ?whole ?assertedPart ?restriction
    """


def _part_of_expansion_branches(code: str) -> tuple[str, str]:
    # Flattened SPARQL rows omit RDF term metadata, so carry target IRI-ness explicitly.
    iri = safe_iri(code, NCIT_NS)
    parent = f"""{{
        BIND(<{iri}> AS ?node)
        <{iri}> rdfs:subClassOf ?target .
        FILTER(!isBlank(?target))
        BIND("parent" AS ?kind)
        BIND(IF(isIRI(?target), "iri", "non-iri") AS ?targetType)
    }}"""
    whole = f"""{{
        BIND(<{iri}> AS ?node)
        <{iri}> rdfs:subClassOf ?restriction .
        ?restriction a owl:Restriction ;
            owl:onProperty <{NCIT_NS}R82> ;
            owl:someValuesFrom ?target .
        BIND("whole" AS ?kind)
        BIND(IF(isIRI(?target), "iri", "non-iri") AS ?targetType)
    }}"""
    return parent, whole


def build_part_of_expansion_query(codes: Iterable[str]) -> str:
    """Build one constant-anchored superclass/R82 expansion request.

    Each code is embedded as the subject of its own branches. This follows the safe
    constant-subject form established against the preflight corpus; a direct query
    using ``VALUES ?node`` exceeded the owned preflight store's 10-second watchdog.
    """
    code_list = _part_of_codes(codes)
    if len(code_list) > _PART_OF_QUERY_CODE_LIMIT:
        raise ValueError("R82 expansion query accepts at most 16 codes")
    if not code_list:
        query = (
            f"{_PREFIXES}SELECT DISTINCT ?node ?kind ?target ?targetType ?restriction "
            f"WHERE {{ FILTER(false) }} LIMIT {_PART_OF_EXPANSION_ROW_LIMIT + 1}"
        )
    else:
        branches = "\nUNION\n".join(
            branch for code in code_list for branch in _part_of_expansion_branches(code)
        )
        query = f"""{_PREFIXES}
            SELECT DISTINCT ?node ?kind ?target ?targetType ?restriction WHERE {{
                GRAPH <{STATED_GRAPH_IRI}> {{
                    {branches}
                }}
            }}
            ORDER BY ?node ?kind ?target ?targetType
            LIMIT {_PART_OF_EXPANSION_ROW_LIMIT + 1}
        """
    query_bytes = len(query.encode())
    if query_bytes > _PART_OF_MAX_QUERY_BYTES:
        raise ValueError(
            f"R82 expansion query body is {query_bytes} bytes; "
            "exceeds 65536-byte safety bound"
        )
    return query


class _PartOfClosure:
    def __init__(self, select_once: SelectRows, requested: tuple[str, ...]) -> None:
        self.select_once = select_once
        self.requested = requested
        self.cache: dict[str, _PartOfNodeExpansion] = {}
        self.expanded_codes: set[str] = set()
        self.request_count = 0
        self.total_rows = 0

    async def _expand(self, frontier: Iterable[str]) -> None:
        missing = sorted(set(frontier) - self.cache.keys())
        if not missing:
            return
        prospective_expanded_codes = self.expanded_codes | set(missing)
        if len(prospective_expanded_codes) > _PART_OF_MAX_EXPANDED_CODES:
            raise ValueError(
                "R82 closure cumulative expanded-code bound exceeds 256 codes"
            )
        self.expanded_codes.update(missing)

        for start in range(0, len(missing), _PART_OF_QUERY_CODE_LIMIT):
            await self._request_tile(missing[start : start + _PART_OF_QUERY_CODE_LIMIT])

    async def _request_tile(self, tile: list[str]) -> None:
        query = build_part_of_expansion_query(tile)
        if self.request_count >= _PART_OF_MAX_REQUESTS:
            raise ValueError("R82 closure request bound exhausted at 64 requests")
        self.request_count += 1
        rows = await self.select_once(
            query,
            required_variables={"node", "kind", "target", "targetType"},
        )
        if len(rows) > _PART_OF_EXPANSION_ROW_LIMIT:
            raise ValueError("R82 closure row bound exceeds 256 rows")
        self.total_rows += len(rows)
        if self.total_rows > _PART_OF_MAX_TOTAL_ROWS:
            raise ValueError("R82 closure total row bound exceeds 4096 rows")
        self._cache_tile(tile, rows)

    def _cache_tile(
        self,
        tile: list[str],
        rows: Sequence[Mapping[str, str | None]],
    ) -> None:
        parents, wholes = _collect_expansion_targets(tile, rows)
        whole_evidence = _collect_whole_evidence(tile, rows)
        self.cache.update(
            {
                code: _PartOfNodeExpansion(
                    parents=frozenset(parents[code]),
                    wholes=frozenset(wholes[code]),
                    whole_evidence=tuple(sorted(whole_evidence[code])),
                )
                for code in tile
            }
        )

    async def _inherited_wholes(self, roots: Iterable[str]) -> dict[str, set[str]]:
        root_list = tuple(sorted(set(roots)))
        visited = {root: {root} for root in root_list}
        frontiers = {root: {root} for root in root_list}
        wholes = {root: set() for root in root_list}

        depth = 0
        while True:
            await self._expand(_frontier_nodes(frontiers))
            next_frontiers = _next_superclass_frontiers(
                self.cache,
                frontiers,
                visited,
                wholes,
            )
            if not _frontier_nodes(next_frontiers):
                return wholes
            if depth == _PART_OF_MAX_SUPERCLASS_HOPS:
                raise ValueError(
                    "R82 superclass hop bound exhausted at "
                    f"{_PART_OF_MAX_SUPERCLASS_HOPS} hops"
                )
            frontiers = next_frontiers
            depth += 1

    async def resolve(self) -> list[PartOfPair]:
        requested_set = frozenset(self.requested)
        reached = {origin: {origin} for origin in self.requested}
        frontiers = {origin: {origin} for origin in self.requested}
        pairs: set[PartOfPair] = set()

        hop = 1
        while True:
            outgoing = await self._inherited_wholes(_frontier_nodes(frontiers))
            next_frontiers = _next_part_of_frontiers(frontiers, outgoing, reached)
            if hop > _PART_OF_MAX_R82_HOPS:
                if _frontier_nodes(next_frontiers):
                    raise ValueError("R82 hop bound exhausted at 8 hops")
                break
            _record_requested_pairs(next_frontiers, reached, requested_set, pairs)
            if not _frontier_nodes(next_frontiers):
                break
            frontiers = next_frontiers
            hop += 1

        return sorted(pairs, key=lambda pair: (pair.part, pair.whole))

    async def inherited_edges(
        self, roots: Iterable[str]
    ) -> dict[str, dict[str, tuple[str, str]]]:
        """Return each root's inherited one-step R82 edges with asserted evidence."""
        root_list = tuple(sorted(set(roots)))
        visited = {root: {root} for root in root_list}
        frontiers = {root: {root} for root in root_list}
        evidence: dict[str, dict[str, tuple[str, str]]] = {
            root: {} for root in root_list
        }
        depth = 0
        while True:
            await self._expand(_frontier_nodes(frontiers))
            next_frontiers = _advance_inherited_evidence(
                self.cache, frontiers, visited, evidence
            )
            if not _frontier_nodes(next_frontiers):
                return evidence
            if depth == _PART_OF_MAX_SUPERCLASS_HOPS:
                raise ValueError("R82 superclass hop bound exhausted at 20 hops")
            frontiers = next_frontiers
            depth += 1


def _collect_expansion_targets(
    tile: list[str], rows: Sequence[Mapping[str, str | None]]
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    tile_set = set(tile)
    parents = {code: set() for code in tile}
    wholes = {code: set() for code in tile}
    for expansion in part_of_expansions_from_rows(rows):
        if expansion.node not in tile_set:
            raise ValueError(f"unexpected R82 expansion node: {expansion.node!r}")
        destination = parents if expansion.kind == "parent" else wholes
        destination[expansion.node].add(expansion.target)
    return parents, wholes


def _collect_whole_evidence(
    tile: list[str], rows: Sequence[Mapping[str, str | None]]
) -> dict[str, set[tuple[str, str]]]:
    evidence: dict[str, set[tuple[str, str]]] = {code: set() for code in tile}
    for row in rows:
        if row.get("kind") == "whole":
            _add_whole_evidence(evidence, row)
    return evidence


def _add_whole_evidence(
    evidence: dict[str, set[tuple[str, str]]], row: Mapping[str, str | None]
) -> None:
    node_iri = row.get("node")
    target_iri = row.get("target")
    restriction = row.get("restriction")
    if not node_iri or not node_iri.startswith(NCIT_NS):
        raise ValueError("R82 expansion node is not an NCIt IRI")
    if not target_iri or not target_iri.startswith(NCIT_NS):
        raise ValueError("R82 expansion target is not an NCIt IRI")
    if restriction:
        evidence[node_iri.removeprefix(NCIT_NS)].add(
            (target_iri.removeprefix(NCIT_NS), restriction)
        )


def _advance_inherited_evidence(
    cache: Mapping[str, _PartOfNodeExpansion],
    frontiers: Mapping[str, set[str]],
    visited: dict[str, set[str]],
    evidence: dict[str, dict[str, tuple[str, str]]],
) -> dict[str, set[str]]:
    next_frontiers: dict[str, set[str]] = {}
    for root, frontier in frontiers.items():
        parents = _collect_inherited_evidence(cache, frontier, evidence[root])
        parents.difference_update(visited[root])
        visited[root].update(parents)
        next_frontiers[root] = parents
    return next_frontiers


def _collect_inherited_evidence(
    cache: Mapping[str, _PartOfNodeExpansion],
    frontier: set[str],
    evidence: dict[str, tuple[str, str]],
) -> set[str]:
    parents: set[str] = set()
    for node in sorted(frontier):
        expansion = cache[node]
        if len(expansion.whole_evidence) != len(expansion.wholes):
            raise ValueError("R82 expansion is missing restriction identity")
        for whole, restriction in expansion.whole_evidence:
            evidence.setdefault(whole, (node, restriction))
        parents.update(expansion.parents)
    return parents


@dataclass(frozen=True, slots=True)
class PartOfPathResolution:
    paths: dict[tuple[str, str], ResolvedR82Path]
    query_count: int
    max_pair_batch_size: int


async def _resolve_path_batch(
    client: SingleAttemptSelectRows,
    pairs: tuple[tuple[str, str], ...],
    source_identity: str,
) -> tuple[dict[tuple[str, str], ResolvedR82Path], int]:
    closure = _path_closure(client, pairs)
    resolved: dict[tuple[str, str], ResolvedR82Path] = {}
    targets_by_origin, paths_by_origin, frontiers, reached = _initial_path_search(pairs)
    for _hop in range(_PART_OF_MAX_R82_HOPS):
        active_nodes = _active_path_nodes(frontiers, targets_by_origin, resolved)
        if not active_nodes:
            break
        outgoing = await closure.inherited_edges(active_nodes)
        next_frontiers = _advance_path_search(
            frontiers,
            targets_by_origin,
            resolved,
            outgoing,
            reached,
            paths_by_origin,
            source_identity,
            ResolvedR82Path,
        )
        frontiers = next_frontiers
    return resolved, closure.request_count


def _path_closure(
    client: SingleAttemptSelectRows, pairs: tuple[tuple[str, str], ...]
) -> _PartOfClosure:
    codes = _part_of_codes(code for pair in pairs for code in pair)
    return _PartOfClosure(select_once=client.select_once, requested=codes)


def _initial_path_search(
    pairs: tuple[tuple[str, str], ...],
) -> tuple[
    dict[str, set[str]],
    dict[str, dict[str, tuple[ResolvedR82PathEdge, ...]]],
    dict[str, set[str]],
    dict[str, set[str]],
]:
    targets = _targets_by_origin(pairs)
    paths = {origin: {origin: ()} for origin in targets}
    frontiers = {origin: {origin} for origin in targets}
    reached = {origin: {origin} for origin in targets}
    return targets, paths, frontiers, reached


def _targets_by_origin(
    pairs: tuple[tuple[str, str], ...],
) -> dict[str, set[str]]:
    targets: dict[str, set[str]] = {}
    for part, whole in pairs:
        targets.setdefault(part, set()).add(whole)
    return targets


def _origin_is_resolved(
    origin: str,
    targets: Mapping[str, set[str]],
    resolved: Mapping[tuple[str, str], ResolvedR82Path],
) -> bool:
    return all((origin, target) in resolved for target in targets[origin])


def _active_path_nodes(
    frontiers: Mapping[str, set[str]],
    targets: Mapping[str, set[str]],
    resolved: Mapping[tuple[str, str], ResolvedR82Path],
) -> set[str]:
    return {
        node
        for origin, frontier in frontiers.items()
        if not _origin_is_resolved(origin, targets, resolved)
        for node in frontier
    }


def _advance_path_search(
    frontiers: Mapping[str, set[str]],
    targets: Mapping[str, set[str]],
    resolved: dict[tuple[str, str], ResolvedR82Path],
    outgoing: Mapping[str, Mapping[str, tuple[str, str]]],
    reached: dict[str, set[str]],
    paths_by_origin: dict[str, dict[str, tuple[ResolvedR82PathEdge, ...]]],
    source_identity: str,
    path_type: type[ResolvedR82Path],
) -> dict[str, set[str]]:
    next_frontiers: dict[str, set[str]] = {}
    for origin, frontier in frontiers.items():
        if _origin_is_resolved(origin, targets, resolved):
            next_frontiers[origin] = set()
            continue
        next_paths = _advance_origin_paths(
            origin,
            frontier,
            outgoing,
            reached[origin],
            paths_by_origin[origin],
            source_identity,
        )
        _record_resolved_targets(
            origin, targets[origin], next_paths, resolved, path_type
        )
        reached[origin].update(next_paths)
        paths_by_origin[origin].update(next_paths)
        next_frontiers[origin] = set(next_paths)
    return next_frontiers


def _advance_origin_paths(
    origin: str,
    frontier: set[str],
    outgoing: Mapping[str, Mapping[str, tuple[str, str]]],
    reached: set[str],
    paths: Mapping[str, tuple[ResolvedR82PathEdge, ...]],
    source_identity: str,
) -> dict[str, tuple[ResolvedR82PathEdge, ...]]:
    next_paths: dict[str, tuple[ResolvedR82PathEdge, ...]] = {}
    for part in sorted(frontier):
        for whole, evidence in sorted(outgoing[part].items()):
            if whole in reached:
                continue
            candidate = (
                *paths[part],
                _make_r82_edge(part, whole, evidence, source_identity),
            )
            current = next_paths.get(whole)
            if current is None or _path_sort_key(candidate) < _path_sort_key(current):
                next_paths[whole] = candidate
    return next_paths


def _make_r82_edge(
    part: str,
    whole: str,
    evidence: tuple[str, str],
    source_identity: str,
) -> ResolvedR82PathEdge:
    asserted_part, restriction = evidence
    return ResolvedR82PathEdge(
        part_code=part,
        asserted_part_code=asserted_part,
        whole_code=whole,
        restriction_node_id=restriction,
        fact_identity=r82_fact_identity(
            source_identity, asserted_part, whole, restriction
        ),
        source_identity=source_identity,
    )


def _path_sort_key(
    path: tuple[ResolvedR82PathEdge, ...],
) -> tuple[tuple[str, str, str], ...]:
    return tuple((item.part_code, item.whole_code, item.fact_identity) for item in path)


def _record_resolved_targets(
    origin: str,
    targets: set[str],
    next_paths: Mapping[str, tuple[ResolvedR82PathEdge, ...]],
    resolved: dict[tuple[str, str], ResolvedR82Path],
    path_type: type[ResolvedR82Path],
) -> None:
    for target in targets:
        if target in next_paths and (origin, target) not in resolved:
            resolved[(origin, target)] = path_type(edges=next_paths[target])


async def resolve_part_of_paths(
    client: SingleAttemptSelectRows,
    pairs: Iterable[tuple[str, str]],
    *,
    source_identity: str,
) -> PartOfPathResolution:
    """Resolve shortest directed stated-R82 paths in batches of at most eight pairs."""
    if re.fullmatch(r"[0-9a-f]{64}", source_identity) is None:
        raise ValueError("R82 path source identity must be SHA-256")
    requested = tuple(sorted(set(pairs)))
    for pair in requested:
        _part_of_codes(pair)
    paths, query_count = await _resolve_direct_paths(client, requested, source_identity)
    remaining = tuple(pair for pair in requested if pair not in paths)
    for start in range(0, len(remaining), 8):
        batch = remaining[start : start + 8]
        batch_paths, batch_queries = await _resolve_path_batch(
            client, batch, source_identity
        )
        paths.update(batch_paths)
        query_count += batch_queries
    return PartOfPathResolution(
        paths=paths,
        query_count=query_count,
        max_pair_batch_size=min(8, len(requested)),
    )


async def _resolve_direct_paths(
    client: SingleAttemptSelectRows,
    requested: tuple[tuple[str, str], ...],
    source_identity: str,
) -> tuple[dict[tuple[str, str], ResolvedR82Path], int]:
    paths: dict[tuple[str, str], ResolvedR82Path] = {}
    query_count = 0
    for start in range(0, len(requested), _PART_OF_CANDIDATE_PREFLIGHT_LIMIT):
        batch = requested[start : start + _PART_OF_CANDIDATE_PREFLIGHT_LIMIT]
        rows = await client.select_once(
            build_part_of_candidate_paths_query(batch),
            required_variables={"part", "whole", "assertedPart", "restriction"},
        )
        query_count += 1
        for row in rows:
            key, candidate = _direct_path_from_row(row, batch, source_identity)
            current = paths.get(key)
            if current is None or _path_sort_key(candidate.edges) < _path_sort_key(
                current.edges
            ):
                paths[key] = candidate
    return paths, query_count


def _direct_path_from_row(
    row: Mapping[str, str | None],
    batch: tuple[tuple[str, str], ...],
    source_identity: str,
) -> tuple[tuple[str, str], ResolvedR82Path]:
    bindings = _required_path_bindings(row)
    part, whole, asserted_part = (
        bindings[name].removeprefix(NCIT_NS)
        for name in ("part", "whole", "assertedPart")
    )
    key = (part, whole)
    if key not in set(batch):
        raise ValueError("R82 candidate path returned an unrequested pair")
    edge = _make_r82_edge(
        part,
        whole,
        (asserted_part, bindings["restriction"]),
        source_identity,
    )
    return key, ResolvedR82Path(edges=(edge,))


def _required_path_bindings(
    row: Mapping[str, str | None],
) -> dict[str, str]:
    names = ("part", "whole", "assertedPart", "restriction")
    bindings = {name: row.get(name) for name in names}
    if any(value is None for value in bindings.values()):
        raise ValueError("R82 candidate path row is missing a binding")
    result = cast("dict[str, str]", bindings)
    if not all(result[name].startswith(NCIT_NS) for name in names[:3]):
        raise ValueError("R82 candidate path is not NCIt-bound")
    return result


async def resolve_part_of_pairs(
    client: SingleAttemptSelectRows,
    codes: Iterable[str],
) -> list[PartOfPair]:
    """Return bounded, non-reflexive transitive R82 reachability within *codes*.

    Intermediate R82 wholes and named superclasses may lie outside the requested set,
    but only requested endpoint pairs are returned. Every store request expands fixed
    one-step edges from constant subjects; cycles and duplicate paths are deduplicated.
    The client's ``select_once`` operation makes at most one transport attempt per
    invocation, so hidden retries cannot bypass the 64-call store-request bound.

    Raises:
        ValueError: for invalid returned expansion bindings or an exhausted bound.
    """
    requested = _part_of_codes(codes)
    if not requested:
        return []
    if len(requested) > _PART_OF_MAX_EXPANDED_CODES:
        raise ValueError("R82 closure cumulative expanded-code bound exceeds 256 codes")
    return await _PartOfClosure(
        select_once=client.select_once,
        requested=requested,
    ).resolve()


def build_morphology_query(concept_code: str) -> str:
    """Fetch label, genus, and semantic type for *concept_code* and its genus chain.

    Returns rows with ``?genus`` (genus code), ``?label`` (genus label), ``?depth``
    (hop count from starting concept), needed to identify the morphology-bearing
    parent (first non-staging genus).

    Raises:
        ValueError: if *concept_code* is not injection-safe.
    """
    concept_uri = safe_iri(concept_code, NCIT_NS)
    semantic_type_uri = f"{NCIT_NS}{SEMANTIC_TYPE}"
    return f"""{_PREFIXES}
        SELECT ?genus ?label ?depth WHERE {{
            GRAPH <{STATED_GRAPH_IRI}> {{
                <{concept_uri}> owl:equivalentClass ?ec .
                ?ec owl:intersectionOf ?list .
                ?list rdf:first ?first .
                ?list rdf:rest*/rdf:first ?genus .
                OPTIONAL {{ ?genus rdfs:label ?label . }}
                OPTIONAL {{ ?genus <{semantic_type_uri}> ?stype . }}
            }}
            BIND(REPLACE(STR(?first), ".*#", "") AS ?first_code)
            BIND(IF(?genus = ?first, 0, 1) AS ?depth)
        }}
    """


def build_role_restrictions_query(concept_code: str) -> str:
    """Role restrictions (``owl:someValuesFrom``) for *concept_code*, stated graph.

    Projects ``?rel`` (property IRI), ``?relLabel`` (its name — the ``Excludes_*`` /
    defining classification keys on), and ``?target`` (the filler concept IRI).

    NOTE: this matches only restrictions hung **directly** off ``rdfs:subClassOf``. In
    the stated build a pre-coordinated concept is a *defined class* whose roles live in
    an ``owl:equivalentClass``/``owl:intersectionOf`` genus chain — those require the
    recursive genus-chain traversal described in
    ``docs/design/ncit-decomposition-engine.md`` §6.1 (next #4 increment). This builder
    is the primitive-class building block for that traversal.

    Raises:
        ValueError: if *concept_code* is not injection-safe.
    """
    concept_uri = safe_iri(concept_code, NCIT_NS)
    return f"""{_PREFIXES}
        SELECT ?rel ?relLabel ?target WHERE {{
            GRAPH <{STATED_GRAPH_IRI}> {{
                <{concept_uri}> rdfs:subClassOf ?restriction .
                ?restriction a owl:Restriction ;
                             owl:onProperty ?rel ;
                             owl:someValuesFrom ?target .
                FILTER(STRSTARTS(STR(?target), "{NCIT_NS}"))
            }}
            # Resolve the property label from the DEFAULT graph (NCIt property
            # definitions live there), not the stated named graph — otherwise the
            # Excludes_* classification silently breaks if the stated graph carries only
            # class axioms without property rdfs:labels.
            OPTIONAL {{ ?rel rdfs:label ?relLabel }}
        }}
    """  # noqa: S608 — interpolated values are safe_iri-validated + module constants


def build_semantic_type_query(concept_code: str) -> str:
    """The ``P106`` semantic-type literal(s) for *concept_code* in the stated graph.

    Raises:
        ValueError: if *concept_code* is not injection-safe.
    """
    concept_uri = safe_iri(concept_code, NCIT_NS)
    semantic_type_uri = f"{NCIT_NS}{SEMANTIC_TYPE}"
    return f"""{_PREFIXES}
        SELECT ?semanticType WHERE {{
            GRAPH <{STATED_GRAPH_IRI}> {{
                <{concept_uri}> <{semantic_type_uri}> ?semanticType .
            }}
        }}
    """


def build_ancestor_pairs_query(codes: Iterable[str]) -> str:
    """Transitive ``rdfs:subClassOf`` (ancestor, descendant) pairs *within* a code set.

    Feeds the most-specific filler selection: both endpoints are restricted to *codes*
    via ``VALUES`` so the result is exactly the intra-set ancestor relationships. An
    empty set produces a valid query that matches nothing.

    Raises:
        ValueError: if any code is not injection-safe.
    """
    iris = " ".join(f"<{safe_iri(code, NCIT_NS)}>" for code in codes)
    return f"""{_PREFIXES}
        SELECT ?ancestor ?descendant WHERE {{
            GRAPH <{STATED_GRAPH_IRI}> {{
                ?descendant rdfs:subClassOf+ ?ancestor .
            }}
            VALUES ?descendant {{ {iris} }}
            VALUES ?ancestor {{ {iris} }}
        }}
    """


def build_in_scope_concepts_query(
    semantic_types: Iterable[str], *, limit: int = 500, offset: int = 0
) -> str:
    """Page through concepts carrying any of *semantic_types* in the stated graph.

    Projects ``?concept`` only (design §9 step 1, "enumerate in-scope concepts").
    Ordered by ``?concept`` so paging by (*limit*, *offset*) is stable across calls.

    Raises:
        ValueError: if any semantic type is not injection-safe.
    """
    literals = " ".join(f'"{_safe_literal(t)}"' for t in semantic_types)
    semantic_type_uri = f"{NCIT_NS}{SEMANTIC_TYPE}"
    return f"""{_PREFIXES}
        SELECT ?concept WHERE {{
            GRAPH <{STATED_GRAPH_IRI}> {{
                ?concept <{semantic_type_uri}> ?semanticType .
            }}
            VALUES ?semanticType {{ {literals} }}
        }}
        ORDER BY ?concept
        LIMIT {limit} OFFSET {offset}
    """


# ── Genus-chain walker (async, needs a select-capable client) ──────────────

# Role codes that carry neoplasm-relevant axis information. Roles outside this
# set found deeper than the starting concept's own level are filtered as
# generic neoplasm biology, not specific to a given concept. Extended as new
# valid axes are validated against the golden oracle.
#
# Labels below are the live NCIt rdfs:label values (verified against the stated
# build, 2026-07). R135 is dropped downstream by filter_excluded. R104/R107 remain
# held from inherited projection pending axis adjudication; R103/R108 are
# source-complete M1 axes with explicit generic suppression downstream.
_CORE_NEOPLASM_ROLES: frozenset[str] = frozenset(
    {
        "R88",  # Disease_Is_Stage
        "R101",  # Disease_Has_Primary_Anatomic_Site
        "R100",  # Disease_Has_Associated_Anatomic_Site
        "R102",  # Disease_Has_Metastatic_Anatomic_Site
        "R103",  # Disease_Has_Normal_Tissue_Origin
        "R105",  # Disease_Has_Abnormal_Cell
        "R106",  # Disease_Has_Molecular_Abnormality
        "R108",  # Disease_Has_Finding
        "R135",  # Disease_Excludes_Primary_Anatomic_Site (see scope note above)
    }
)


_STAGING_LABEL_MARKERS = frozenset(
    {
        "Stage I",
        "Stage II",
        "Stage III",
        "Stage IV",
        "AJCC",
        " v7",
        " v8",
        "Unresectable",
        "Recurrent",
        "Metastatic",
        " by ",  # "by AJCC v7 Stage"
    }
)


def _is_staging_concept_label(label: str) -> bool:
    """True if *label* matches a staging qualifier pattern."""
    label_lower = label.lower()
    return any(m.lower() in label_lower for m in _STAGING_LABEL_MARKERS)


def _required_row_binding(row: Mapping[str, str | None], binding: str) -> str:
    value = row.get(binding)
    if not value:
        raise ValueError(f"query result row is missing required {binding} binding")
    return value


def _genus_code_from_iri(genus_iri: str) -> str:
    if not genus_iri.startswith(NCIT_NS):
        raise ValueError("genus member is not an NCIt IRI")
    code = genus_iri.removeprefix(NCIT_NS)
    if _NCIT_CONCEPT_CODE.fullmatch(code) is None:
        raise ValueError(f"genus member is not an NCIt concept code: {code!r}")
    return code


async def _fetch_genus_label(
    select_fn: SelectRows,
    genus_iri: str,
) -> str | None:
    """Fetch the label for a genus concept from the stated graph."""
    label_query = f"""{_PREFIXES}
        SELECT ?label WHERE {{
            GRAPH <{STATED_GRAPH_IRI}> {{
                <{genus_iri}> rdfs:label ?label .
            }}
        }}
    """
    rows = await select_fn(label_query, required_variables={"label"})
    if not rows:
        return None
    labels = {_required_row_binding(row, "label") for row in rows}
    if len(labels) != 1:
        raise ValueError("genus concept has multiple distinct stated labels")
    return next(iter(labels))


async def _get_genus_from_intersection(
    select_fn: SelectRows,
    code: str,
) -> str | None:
    """Get the genus code from the first owl:intersectionOf member."""
    queries = build_genus_walk_members_query(code)

    rows = await select_fn(
        queries[0], required_variables={"member"}
    )  # hop-0: first intersection member
    if not rows:
        return None

    genuses: set[str] = set()
    for row in rows:
        genus_iri = _required_row_binding(row, "member")
        if row.get("type") == OWL_NS + "Restriction":
            continue
        genuses.add(_genus_code_from_iri(genus_iri))
    if len(genuses) > 1:
        raise ValueError("intersection has multiple named genus members")
    return next(iter(genuses)) if genuses else None


async def resolve_morphology_filler(
    select_fn: SelectRows,
    code: str,
    *,
    max_depth: int = 5,
) -> str | None:
    """Resolve the morphology filler from the genus chain of *code*.

    Walks the genus chain, returning the first non-staging genus code.
    Staging concepts are identified by labels containing stage markers
    (Stage I-IV, AJCC, v7/v8, Unresectable, etc.).

    Returns ``None`` if no morphology-bearing genus is found within max_depth.
    """
    visited: set[str] = {code}
    current_code = code

    for _ in range(max_depth):
        genus_code = await _get_genus_from_intersection(select_fn, current_code)
        if not genus_code:
            return None

        if genus_code in visited:
            return None
        visited.add(genus_code)

        genus_iri = f"{NCIT_NS}{genus_code}"
        label = await _fetch_genus_label(select_fn, genus_iri)

        if label is not None and not _is_staging_concept_label(label):
            return genus_code

        current_code = genus_code

    return None


def _definition_genera_by_anchor(
    complete: CompleteDefinition, max_depth: int
) -> dict[str, set[str]]:
    genera_by_anchor: dict[str, set[str]] = {}
    for fact in complete.facts:
        if isinstance(fact, GenusDefinitionFact) and fact.depth < max_depth:
            genera_by_anchor.setdefault(fact.anchor_code, set()).add(fact.genus_code)
    return genera_by_anchor


async def _resolve_morphology_frontier(
    select_fn: SelectRows,
    frontier: tuple[str, ...],
    genera_by_anchor: Mapping[str, set[str]],
    visited: set[str],
    selected: set[str],
    preferred: str | None,
) -> tuple[str, ...]:
    next_frontier: set[str] = set()
    for anchor in frontier:
        for genus_code in sorted(genera_by_anchor.get(anchor, ())):
            if genus_code in visited:
                continue
            visited.add(genus_code)
            if genus_code == preferred:
                selected.add(genus_code)
                continue
            label = await _fetch_genus_label(select_fn, f"{NCIT_NS}{genus_code}")
            if label is not None and not _is_staging_concept_label(label):
                selected.add(genus_code)
            else:
                next_frontier.add(genus_code)
    return tuple(sorted(next_frontier))


async def resolve_morphology_fillers(
    select_fn: SelectRows,
    complete: CompleteDefinition,
    *,
    max_depth: int = 5,
) -> tuple[str, ...]:
    """Resolve every co-equal first non-staging genus in a complete definition.

    Anonymous nested intersections can place more than one named genus at the same
    anchor.  Walking only the first RDF-list member loses those co-equal source facts,
    so this projection follows all genus facts already validated by the complete reader.
    """
    preferred = await resolve_morphology_filler(
        select_fn, complete.root_code, max_depth=max_depth
    )
    genera_by_anchor = _definition_genera_by_anchor(complete, max_depth)

    selected: set[str] = set()
    visited = {complete.root_code}
    frontier = (complete.root_code,)
    for _ in range(max_depth):
        if not frontier:
            break
        frontier = await _resolve_morphology_frontier(
            select_fn,
            frontier,
            genera_by_anchor,
            visited,
            selected,
            preferred,
        )
    return (
        *((preferred,) if preferred in selected else ()),
        *(code for code in sorted(selected) if code != preferred),
    )


def _build_role_labels_query(role_codes: Iterable[str]) -> str:
    iris = " ".join(f"<{safe_iri(code, NCIT_NS)}>" for code in sorted(role_codes))
    return f"""{_PREFIXES}
        SELECT ?role ?roleLabel WHERE {{
            VALUES ?role {{ {iris} }}
            OPTIONAL {{ ?role rdfs:label ?roleLabel }}
        }}
        ORDER BY STR(?role) STR(?roleLabel)
    """


async def _definition_role_labels(
    select_fn: SelectRows,
    role_codes: set[str],
) -> dict[str, str | None]:
    if not role_codes:
        return {}
    rows = await select_fn(
        _build_role_labels_query(role_codes),
        required_variables={"role"},
    )
    labels: dict[str, str | None] = {}
    for row in rows:
        _record_definition_role_label(labels, role_codes, row)
    return labels


def _record_definition_role_label(
    labels: dict[str, str | None],
    requested_codes: set[str],
    row: Mapping[str, str | None],
) -> None:
    role_code = _validated_definition_role_code(row, requested_codes)
    _merge_definition_role_label(labels, role_code, row.get("roleLabel"))


def _validated_definition_role_code(
    row: Mapping[str, str | None],
    requested_codes: set[str],
) -> str:
    role_iri = _required_row_binding(row, "role")
    if not role_iri.startswith(NCIT_NS):
        raise ValueError("role label row is not an NCIt IRI")
    role_code = role_iri.removeprefix(NCIT_NS)
    if role_code not in requested_codes:
        raise ValueError("role label query returned an unrequested role")
    return role_code


def _merge_definition_role_label(
    labels: dict[str, str | None],
    role_code: str,
    label: str | None,
) -> None:
    previous = labels.setdefault(role_code, label)
    if previous != label and previous is not None and label is not None:
        raise ValueError(f"role {role_code} has conflicting labels")
    if previous is None and label is not None:
        labels[role_code] = label


def _projected_restriction_facts(
    complete: CompleteDefinition,
    max_depth: int,
) -> list[RestrictionDefinitionFact]:
    return sorted(
        (
            fact
            for fact in complete.facts
            if isinstance(fact, RestrictionDefinitionFact) and fact.depth < max_depth
        ),
        key=lambda fact: (fact.depth, fact.anchor_code, fact.fact_id),
    )


def _detector_role_projection(
    complete: CompleteDefinition,
    restrictions: Iterable[RestrictionDefinitionFact],
    labels: Mapping[str, str | None],
) -> list[RoleRestriction]:
    occurrences_by_fact: dict[str, list[str]] = {}
    for occurrence in complete.occurrences:
        occurrences_by_fact.setdefault(occurrence.source_fact_id, []).append(
            occurrence.occurrence_id
        )
    return [
        RoleRestriction(
            role_code=fact.role_code,
            filler_code=fact.filler_code,
            role_label=labels.get(fact.role_code),
            anchoring_genus=fact.anchor_code,
            source_definition_ids=(fact.fact_id,),
            source_occurrence_ids=tuple(occurrences_by_fact.get(fact.fact_id, ())),
        )
        for fact in restrictions
        if _is_detector_role(fact)
    ]


def _is_detector_role(fact: RestrictionDefinitionFact) -> bool:
    return fact.depth == 0 or fact.role_code in _CORE_NEOPLASM_ROLES


async def read_complete_genus_chain(
    select_fn: SelectRows,
    code: str,
    *,
    max_depth: int = 5,
) -> tuple[CompleteDefinition, list[RoleRestriction]]:
    """Return the complete definition and its detector-compatible role projection.

    The former six-position query loop silently truncated longer intersections and
    rejected anonymous nested intersection groups as non-NCIt genera. Reusing the
    proof-bearing reader gives detection and projection the same complete structure.
    ``max_depth`` limits only the detector-compatible role projection; the complete
    record retains its independent fail-closed named-definition depth bound.
    """
    complete = await read_complete_definition(select_fn, code)
    restrictions = _projected_restriction_facts(complete, max_depth)
    labels = await _definition_role_labels(
        select_fn,
        {fact.role_code for fact in restrictions},
    )
    return complete, _detector_role_projection(complete, restrictions, labels)


async def walk_genus_chain(
    select_fn: SelectRows,
    code: str,
    *,
    max_depth: int = 5,
) -> list[RoleRestriction]:
    """Return detector-compatible roles from the complete bounded stated record.

    A thin projection of :func:`read_complete_genus_chain`, which is what the
    pipeline calls. No production caller remains, but this is the surface the
    real-corpus contract tests (``test_stated_integration``,
    ``test_ncit_sibling_store_integration``) assert against, so it exercises the
    production walk while keeping those assertions about roles alone.
    """
    _complete, roles = await read_complete_genus_chain(
        select_fn,
        code,
        max_depth=max_depth,
    )
    return roles
