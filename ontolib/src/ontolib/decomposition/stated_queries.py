"""SPARQL builders that read the **stated** NCIt named graph (design §3, §2).

Extraction must run off the stated (asserted) OWL, never the inferred default graph,
to avoid ancestor-closure bleed and the ``Excludes_*`` negative axioms (assessment §4).
These builders reuse the restriction-traversal pattern from ``role_queries.py`` wrapped
in a ``GRAPH <STATED_GRAPH_IRI>`` clause, and reuse ``safe_iri`` for injection safety.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ontolib.decomposition.models import RoleRestriction

from ontolib.decomposition.extract import genus_walk_rows_to_roles_and_genuses
from ontolib.terminologies.namespaces import NCIT_NS, OWL_NS, RDF_NS, RDFS_NS
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI
from ontolib.terminologies.ncit.property_codes import SEMANTIC_TYPE
from ontolib.terminologies.oxigraph_http_client import safe_iri

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

_PREFIXES = f"""
        PREFIX rdfs: <{RDFS_NS}>
        PREFIX rdf: <{RDF_NS}>
        PREFIX owl: <{OWL_NS}>
"""

# Maximum rdf:rest hops inside an owl:intersectionOf list. Lists are 2-3 members;
# 6 leaves generous margin for deeply nested lists without sending unbounded rdf:rest*
# queries that the engine may plan poorly on a large stated graph.
_MAX_INTERSECTION_HOPS = 6

# A semantic type is a plain-text SPARQL literal (not an IRI, so ``safe_iri`` does not
# apply): reject anything that could close the literal or inject a graph pattern.
_SAFE_LITERAL = re.compile(r'^[^"\\\n{}]+$')


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

    Uses ONLY individual triple patterns (no property paths) so Oxigraph
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
    Oxigraph's query planner anchored on the subject concept.

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
                OPTIONAL {{ ?role rdfs:label ?roleLabel }}
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


def build_part_of_pairs_query(codes: list[str]) -> str:
    """Transitive R82 ``Part_Of`` restriction pairs *within* a code set.

    For each *code* in *codes*, follows ``rdfs:subClassOf+`` to the nearest
    ancestor that carries an ``R82`` part-of restriction, then returns the
    ``(whole, part)`` pair. Both endpoints are restricted to *codes* so the
    result is only intra-set relationships.

    Raises:
        ValueError: if any code is not injection-safe.
    """
    if not codes:
        return f"{_PREFIXES}SELECT ?whole ?part WHERE {{ BIND(false AS ?ok) }}"
    iris = " ".join(f"<{safe_iri(code, NCIT_NS)}>" for code in codes)
    return f"""{_PREFIXES}
        SELECT DISTINCT ?whole ?part WHERE {{
            GRAPH <{STATED_GRAPH_IRI}> {{
                VALUES ?descendant {{ {iris} }}
                ?descendant rdfs:subClassOf* ?ancestor .
                ?ancestor rdfs:subClassOf ?restriction .
                ?restriction a owl:Restriction ;
                    owl:onProperty <{NCIT_NS}R82> ;
                    owl:someValuesFrom ?part .
                FILTER(STRSTARTS(STR(?part), "{NCIT_NS}"))
                BIND(REPLACE(STR(?part), ".*#", "") AS ?part_code)
            }}
            VALUES ?part_code {{ {iris} }}
            VALUES ?descendant {{ {iris} }}
            BIND(REPLACE(STR(?descendant), ".*#", "") AS ?whole)
        }}
    """


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
_CORE_NEOPLASM_ROLES: frozenset[str] = frozenset(
    {
        "R88",  # Disease_Has_Associated_Site
        "R101",  # Disease_Has_Normal_Cell_Origin
        "R100",  # Disease_Has_Primary_Anatomic_Site
        "R102",  # Disease_Has_Associated_Anatomic_Site
        "R105",  # Disease_Has_Abnormal_Cell
        "R106",  # (stage)
        "R135",  # Disease_Has_Grade
    }
)


def _flatten_hop_results(
    results: Iterable[list[dict[str, str | None]]],
) -> list[dict[str, str | None]]:
    flat: list[dict[str, str | None]] = []
    for rows in results:
        if not rows:
            break
        flat.extend(rows)
    return flat


def _collect_new_roles(
    roles: list[RoleRestriction],
    depth: int,
    seen: set[tuple[str, str]],
    dest: list[RoleRestriction],
) -> None:
    for r in roles:
        key = (r.role_code, r.filler_code)
        if (depth == 0 or r.role_code in _CORE_NEOPLASM_ROLES) and key not in seen:
            seen.add(key)
            dest.append(r)


async def _process_walk_node(
    select_fn: Callable[[str], Awaitable[list[dict[str, str | None]]]],
    current: str,
    depth: int,
    seen_pairs: set[tuple[str, str]],
    all_roles: list[RoleRestriction],
    visited: set[str],
    next_frontier: list[str],
) -> None:
    queries = build_genus_walk_members_query(current)
    results = await asyncio.gather(
        *(select_fn(q) for q in queries), return_exceptions=False
    )
    member_rows = _flatten_hop_results(results)
    if not member_rows:
        return

    roles, genuses = genus_walk_rows_to_roles_and_genuses(member_rows)
    _collect_new_roles(roles, depth, seen_pairs, all_roles)

    for g in genuses:
        if g not in visited:
            visited.add(g)
            next_frontier.append(g)


async def resolve_starting_genus(
    select_fn: Callable[[str], Awaitable[list[dict[str, str | None]]]],
    code: str,
) -> str | None:
    """Resolve the immediate genus (first ``owl:intersectionOf`` member) of
    *code*, or ``None`` if *code* is a primitive class with no
    ``owl:equivalentClass`` axiom."""
    queries = build_genus_walk_members_query(code)
    if not queries:
        return None
    rows = await select_fn(queries[0])  # hop-0 only
    for row in rows:
        if row.get("type") != OWL_NS + "Restriction":
            genus_iri = row.get("member")
            if genus_iri and genus_iri.startswith(NCIT_NS):
                return genus_iri.removeprefix(NCIT_NS)
            if genus_iri:
                return genus_iri
    return None


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


async def _fetch_genus_label(
    select_fn: Callable[[str], Awaitable[list[dict[str, str | None]]]],
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
    rows = await select_fn(label_query)
    if not rows:
        return None
    return rows[0].get("label")


async def _get_genus_from_intersection(
    select_fn: Callable[[str], Awaitable[list[dict[str, str | None]]]],
    code: str,
) -> str | None:
    """Get the genus code from the first owl:intersectionOf member."""
    queries = build_genus_walk_members_query(code)
    if not queries:
        return None

    rows = await select_fn(queries[0])  # hop-0: first intersection member
    if not rows:
        return None

    for row in rows:
        if row.get("type") != OWL_NS + "Restriction":
            genus_iri = row.get("member")
            if genus_iri and genus_iri.startswith(NCIT_NS):
                return genus_iri.removeprefix(NCIT_NS)
    return None


async def resolve_morphology_filler(
    select_fn: Callable[[str], Awaitable[list[dict[str, str | None]]]],
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


async def walk_genus_chain(
    select_fn: Callable[[str], Awaitable[list[dict[str, str | None]]]],
    code: str,
    *,
    max_depth: int = 5,
) -> list[RoleRestriction]:
    all_roles: list[RoleRestriction] = []
    seen_pairs: set[tuple[str, str]] = set()
    visited: set[str] = {code}
    frontier: list[str] = [code]

    for depth in range(max_depth):
        if not frontier:
            break
        next_frontier: list[str] = []
        for current in frontier:
            await _process_walk_node(
                select_fn, current, depth, seen_pairs, all_roles, visited, next_frontier
            )
        frontier = next_frontier

    return all_roles
