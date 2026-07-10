#!/usr/bin/env python3
"""Research script (#44 continuation): does combining is-a (rdfs:subClassOf+) with
part-of (R82 Anatomic_Structure_Is_Physical_Part_Of, transitive) resolve anatomy-axis
ambiguity without an external Uberon cross-check? Validates the hypothesis from
tmp/PLAN_44.md's R101 finding against more concepts before it's written into
docs/design/ncit-decomposition-engine.md.

R82 restrictions are NOT transitively materialized in the inferred graph (unlike role
restrictions on defined disease classes), so the part-of closure is walked by hand,
hop by hop, the same way as the intersectionOf list walker.
"""

from __future__ import annotations

import asyncio
import sys

from ontolib.decomposition.extract import ancestor_pairs_from_rows, make_is_ancestor
from ontolib.decomposition.filler_selection import most_specific
from ontolib.decomposition.stated_queries import build_ancestor_pairs_query
from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient, safe_iri

_R82 = "R82"
_MAX_HOPS = 8


async def _part_of_targets(client: OxigraphHttpClient, code: str) -> list[str]:
    c = safe_iri(code, NCIT_NS)
    rows = await client.select(
        f"""
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        PREFIX owl: <http://www.w3.org/2002/07/owl#>
        SELECT ?target WHERE {{
            <{c}> rdfs:subClassOf ?r .
            ?r a owl:Restriction ; owl:onProperty <{NCIT_NS}{_R82}> ;
                owl:someValuesFrom ?target .
        }}
        """
    )
    return [r["target"].rsplit("#", 1)[-1] for r in rows if r.get("target")]


async def part_of_ancestor_pairs(
    client: OxigraphHttpClient, codes: set[str]
) -> set[tuple[str, str]]:
    """(ancestor, descendant) pairs among *codes* reachable via transitive R82, walked
    hop-by-hop from each code (R82 is not materialized transitively in this build)."""
    pairs: set[tuple[str, str]] = set()
    for start in codes:
        frontier = {start}
        seen = {start}
        for _ in range(_MAX_HOPS):
            next_frontier: set[str] = set()
            for node in frontier:
                for target in await _part_of_targets(client, node):
                    if target in codes and target != start:
                        pairs.add((target, start))  # target is ancestor of start
                    if target not in seen:
                        seen.add(target)
                        next_frontier.add(target)
            frontier = next_frontier
            if not frontier:
                break
    return pairs


async def resolve(client: OxigraphHttpClient, codes: set[str]) -> tuple[set[str], dict]:
    """Return (leaves, debug) using is-a UNION transitive part-of as 'is_ancestor'."""
    isa_rows = await client.select(build_ancestor_pairs_query(codes))
    isa_pairs = ancestor_pairs_from_rows(isa_rows)
    po_pairs = await part_of_ancestor_pairs(client, codes)
    combined = isa_pairs | po_pairs
    leaves = most_specific(codes, make_is_ancestor(combined))
    return leaves, {"isa_pairs": isa_pairs, "part_of_pairs": po_pairs}


async def main() -> None:
    codes = set(sys.argv[1:])
    async with OxigraphHttpClient("http://localhost:7888") as client:
        leaves, debug = await resolve(client, codes)
        print(f"candidates: {sorted(codes)}")
        print(f"is-a pairs: {sorted(debug['isa_pairs'])}")
        print(f"part-of pairs (walked): {sorted(debug['part_of_pairs'])}")
        print(f"leaves (is-a UNION part-of): {sorted(leaves)}")


if __name__ == "__main__":
    asyncio.run(main())
