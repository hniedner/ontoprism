"""Render SSSOM records to additive Turtle, loaded into the xref named graph."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ontolib.terminologies.namespaces import NCIT_NS

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ontolib.repositories.xref.models import SSSOMRecord

# Map an upstream CURIE prefix to its base IRI (extend as sources are added).
_PREFIX_BASE = {
    "UBERON": "http://purl.obolibrary.org/obo/UBERON_",
    "CL": "http://purl.obolibrary.org/obo/CL_",
}


def _object_iri(curie: str) -> str:
    if ":" not in curie:
        raise ValueError(f"object_id is not a CURIE (missing ':'): {curie!r}")
    prefix, _, local = curie.partition(":")
    base = _PREFIX_BASE[prefix]
    return f"<{base}{local}>"


def render_ttl(records: Iterable[SSSOMRecord]) -> str:
    lines: list[str] = []
    for r in records:
        subj = f"<{NCIT_NS}{r.subject_id}>"
        lines.append(f"{subj} <{r.predicate_id}> {_object_iri(r.object_id)} .")
    return "\n".join(lines) + "\n"
