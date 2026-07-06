#!/usr/bin/env python3
"""EXPERIMENTAL research spike — score a candidate stated-extraction vs the golden set.

Correct extraction of stated pre-coordination is curation-heavy (engine design §6.2): a
genus-chain walk over-collects and most-specific can pick the wrong filler. This is the
iteration harness for that research, NOT a production extractor. It walks the stated
equivalentClass/intersectionOf genus chain, keeps positive defining roles, and scores
precision/recall against ontolib/tests/decomposition/golden/neoplasm.json.

Needs the stated NCIt graph loaded (docs/DATA_SETUP.md). Manual/offline; not in CI.

    pdm run decompose-spike
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from backend.config import get_settings
from ontolib.decomposition.axes import is_defining_role
from ontolib.decomposition.extract import roles_from_rows
from ontolib.decomposition.models import RoleRestriction
from ontolib.decomposition.score import score
from ontolib.terminologies.namespaces import NCIT_NS, OWL_NS, RDF_NS, RDFS_NS
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI
from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient, safe_iri

_GOLDEN = Path(__file__).resolve().parents[1] / (
    "ontolib/tests/decomposition/golden/neoplasm.json"
)
# Non-defining role families to drop (beyond Excludes_*, which axes already drops).
_NON_DEFINING = ("May_", "Mapped_")
_Pair = tuple[str, str]
_EXTRA_PREVIEW = 10  # cap the over-collection list printed per concept


def _level_query(code: str) -> str:
    """One genus-chain level: restrictions + named (defined-class) genus members."""
    c = safe_iri(code, NCIT_NS)
    eq = "owl:equivalentClass/owl:intersectionOf/rdf:rest*/rdf:first"
    return f"""
PREFIX rdfs: <{RDFS_NS}>
PREFIX rdf: <{RDF_NS}>
PREFIX owl: <{OWL_NS}>
SELECT ?rel ?relLabel ?target ?genus WHERE {{
  GRAPH <{STATED_GRAPH_IRI}> {{
    {{
      <{c}> {eq} ?r .
      ?r a owl:Restriction ; owl:onProperty ?rel ; owl:someValuesFrom ?target .
      FILTER(STRSTARTS(STR(?target), "{NCIT_NS}"))
    }} UNION {{
      <{c}> {eq} ?genus .
      ?genus a owl:Class .
      FILTER(isIRI(?genus) && STRSTARTS(STR(?genus), "{NCIT_NS}"))
    }}
  }}
  OPTIONAL {{ ?rel rdfs:label ?relLabel }}
}}
"""


def _defining(role_label: str | None) -> bool:
    """Positive defining role: not Excludes_* (via axes) and not May_/Mapped_."""
    shim = RoleRestriction(role_code="", filler_code="", role_label=role_label)
    if not is_defining_role(shim):
        return False
    return not (role_label and any(m in role_label for m in _NON_DEFINING))


async def _extract(
    client: OxigraphHttpClient, code: str, *, max_nodes: int = 80
) -> set[_Pair]:
    """Experimental genus-chain extraction → set of (axis_code, filler_code) pairs."""
    seen: set[str] = set()
    pairs: set[_Pair] = set()
    stack = [code]
    while stack and len(seen) < max_nodes:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        rows = await client.select(_level_query(current))
        for r in roles_from_rows(rows):
            if _defining(r.role_label):
                pairs.add((r.role_code, r.filler_code))
        for row in rows:
            genus = row.get("genus")
            if genus:
                stack.append(genus.rsplit("#", 1)[-1])
    return pairs


async def main() -> None:
    golden = json.loads(_GOLDEN.read_text())["concepts"]
    settings = get_settings()
    async with OxigraphHttpClient(settings.ncit_sparql_url) as client:
        agg_tp = agg_exp = agg_act = 0
        for code, entry in golden.items():
            expected = {(a, f) for a, f in entry["constituents"]}
            actual = await _extract(client, code)
            s = score(expected, actual)
            agg_tp += s.true_positive
            agg_exp += s.expected
            agg_act += s.actual
            extra = sorted(s.extra)
            print(f"\n{code} — {entry.get('label', '')}")
            print(f"  precision={s.precision:.2f} recall={s.recall:.2f} f1={s.f1:.2f}")
            print(f"  missing: {sorted(s.missing)}")
            tail = " …" if len(extra) > _EXTRA_PREVIEW else ""
            print(f"  extra ({len(extra)}): {extra[:_EXTRA_PREVIEW]}{tail}")
        micro_p = agg_tp / agg_act if agg_act else 1.0
        micro_r = agg_tp / agg_exp if agg_exp else 1.0
        print(
            f"\nAGGREGATE micro precision={micro_p:.2f} recall={micro_r:.2f} "
            f"(tp={agg_tp} expected={agg_exp} actual={agg_act})"
        )


if __name__ == "__main__":
    asyncio.run(main())
