#!/usr/bin/env python3
"""Research script (#44): walk a defined class's genus chain level by level, where
each level's query is freshly anchored at a NAMED class code (never a blank node
carried across separate HTTP requests — re-querying a returned blank node's label
in a follow-up request produced a multi-hundred-MB runaway match against this store,
almost certainly because blank node identifiers are not stable/meaningful to reuse
across separate protocol requests). Also avoids `rest*` inside owl:intersectionOf
(DECISIONS D13 — Oxigraph cannot evaluate that), using incrementing exact-hop paths
(`rdf:rest/rdf:first`, `rdf:rest/rdf:rest/rdf:first`, ...) instead, each anchored at
the concept itself, until a hop returns nothing.

Prints, per level: the named genus class walked into, and the own-differentia roles
found strictly AT that level (i.e., roles in that level's intersectionOf list, never
the ones inherited from further up the chain) — this is the "per-level differentia
diffing" boundary the engine design (§6.2) proposed as the fix for the naive
genus-walk's over-collection.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field

from ontolib.terminologies.namespaces import NCIT_NS, OWL_NS, RDF_NS, RDFS_NS
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI
from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient, safe_iri

_PREFIXES = f"""
PREFIX rdfs: <{RDFS_NS}>
PREFIX rdf: <{RDF_NS}>
PREFIX owl: <{OWL_NS}>
"""
_MAX_HOPS = 15  # generous bound on intersectionOf list length; lists seen so far are 2


@dataclass
class Role:
    code: str
    label: str | None
    filler_code: str
    filler_label: str | None


@dataclass
class Level:
    genus_codes: list[tuple[str, str | None, bool]]  # (code, label, is_defined)
    roles: list[Role] = field(default_factory=list)


def _code(iri: str) -> str:
    return iri.rsplit("#", 1)[-1]


async def _hop_first(client: OxigraphHttpClient, code: str, hops: int) -> list[dict]:
    c = safe_iri(code, NCIT_NS)
    path = "/".join(["rdf:rest"] * hops + ["rdf:first"]) if hops else "rdf:first"
    return await client.select(
        f"""{_PREFIXES}
        SELECT (
            ?member AS ?member
        ) (?is_role) (?rel) (?rellabel) (?target) (?tlabel) (?label)
        (?is_defined) WHERE {{
            GRAPH <{STATED_GRAPH_IRI}> {{
                <{c}> owl:equivalentClass/owl:intersectionOf/{path} ?member .
                OPTIONAL {{
                    ?member a owl:Restriction ; owl:onProperty ?rel ;
                        owl:someValuesFrom ?target .
                    BIND(true AS ?is_role)
                    OPTIONAL {{ ?rel rdfs:label ?rellabel }}
                    OPTIONAL {{ ?target rdfs:label ?tlabel }}
                }}
                OPTIONAL {{ ?member owl:equivalentClass ?eq .
                    BIND(true AS ?is_defined) }}
                OPTIONAL {{ ?member rdfs:label ?label }}
            }}
        }}
        """
    )


async def one_level(client: OxigraphHttpClient, code: str) -> Level | None:
    """The direct (one-hop) intersectionOf members of *code* — None if not defined.

    NCIt's stated pre-coordination hierarchy is a multi-parent DAG, not a linear
    chain: a level frequently has *multiple* named-class genus members (multiple
    inheritance), not just one. All of them are collected here.
    """
    roles: list[Role] = []
    genera: list[tuple[str, str | None, bool]] = []
    for hop in range(_MAX_HOPS):
        rows = await _hop_first(client, code, hop)
        if not rows:
            break
        row = rows[0]
        member = row.get("member")
        if not member:
            break
        if row.get("is_role"):
            roles.append(
                Role(
                    code=_code(row["rel"]),
                    label=row.get("rellabel"),
                    filler_code=_code(row["target"]),
                    filler_label=row.get("tlabel"),
                )
            )
        else:
            genera.append(
                (_code(member), row.get("label"), bool(row.get("is_defined")))
            )
    if not genera and not roles:
        return None
    return Level(genus_codes=genera, roles=roles)


async def _process_frontier(
    client: OxigraphHttpClient,
    current: str,
    visited: set[str],
    next_frontier: list[str],
) -> Level | None:
    level = await one_level(client, current)
    if level is None:
        return None
    for g_code, _label, is_defined in level.genus_codes:
        if is_defined and g_code not in visited:
            visited.add(g_code)
            next_frontier.append(g_code)
    return level


async def walk_chain(
    client: OxigraphHttpClient, code: str, *, max_depth: int = 10
) -> list[Level]:
    """Walk the full genus DAG breadth-first: recurse into every DEFINED genus member.

    Each defined genus is visited once (memoized) — the DAG re-converges (e.g. two
    branches both eventually reach "Malignant Neoplasm"), and re-walking a node
    already visited would just re-collect the same roles redundantly.
    """
    levels: list[Level] = []
    visited: set[str] = {code}
    frontier = [code]
    for _ in range(max_depth):
        if not frontier:
            break
        next_frontier: list[str] = []
        for current in frontier:
            level = await _process_frontier(client, current, visited, next_frontier)
            if level is not None:
                levels.append(level)
        frontier = next_frontier
    return levels


async def main() -> None:
    code = sys.argv[1] if len(sys.argv) > 1 else "C6135"
    async with OxigraphHttpClient("http://localhost:7888") as client:
        levels = await walk_chain(client, code)
        print(f"{code}: {len(levels)} level(s) visited in the genus DAG\n")
        for i, level in enumerate(levels):
            genera = ", ".join(
                f"{g}({'DEFINED' if d else 'PRIMITIVE'})"
                for g, _lbl, d in level.genus_codes
            )
            print(f"Level {i}: genus/genera = {genera or '(none)'}")
            for r in level.roles:
                print(f"    {r.code} ({r.label}) -> {r.filler_code} ({r.filler_label})")
            print()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
