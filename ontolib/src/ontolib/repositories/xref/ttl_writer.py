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

# The upstream sources we can round-trip CURIE <-> IRI. Anything outside this set must
# never reach a validation merge: `object_iri` raises KeyError for it, and that would
# abort a whole promotion run.
SUPPORTED_PREFIXES = tuple(sorted(_PREFIX_BASE))


def object_iri(curie: str) -> str:
    """Expand an upstream CURIE to its full IRI (``UBERON:0002048`` -> ``http://…``).

    Raises ``ValueError`` for a non-CURIE and ``KeyError`` for a prefix we have no
    base IRI for — an unknown source must fail loudly, never be silently dropped.
    """
    if ":" not in curie:
        raise ValueError(f"object_id is not a CURIE (missing ':'): {curie!r}")
    prefix, _, local = curie.partition(":")
    return f"{_PREFIX_BASE[prefix]}{local}"


def _object_iri(curie: str) -> str:
    return f"<{object_iri(curie)}>"


def _endpoint_iri(system: str, identifier: str) -> str:
    if system == "ncit":
        return f"<{NCIT_NS}{identifier}>"
    if system in {"uberon", "uberon-cl"}:
        return _object_iri(identifier)
    raise KeyError(f"unsupported xref endpoint system: {system}")


def render_ttl(records: Iterable[SSSOMRecord]) -> str:
    lines: list[str] = []
    for r in records:
        subject = _endpoint_iri(r.subject_system, r.subject_id)
        obj = _endpoint_iri(r.object_system, r.object_id)
        lines.append(f"{subject} <{r.predicate_id}> {obj} .")
    return "\n".join(lines) + "\n"
