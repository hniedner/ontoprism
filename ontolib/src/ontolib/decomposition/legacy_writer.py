"""Write additive decomposition triples to a TTL file (design §8).

Pure function: takes decompositions and writes RDF/Turtle to stdout or a file path.
Emits plain, graph-agnostic Turtle triples — it has no concept of "which named graph"
and never emits a ``DELETE``; the caller loads the output into ``DECOMPOSED_GRAPH_IRI``
(see ``scripts/decompose.py``'s ``client.load(..., graph_iri=...)``). The source graphs
are never referenced in the output at all.  Uses the op: vocabulary from
:mod:`ontolib.decomposition.vocab`.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from ontolib.decomposition import vocab
from ontolib.terminologies.namespaces import NCIT_NS, OWL_NS

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ontolib.decomposition.models import Decomposition


def _filler_iri(code: str) -> str:
    """Map a filler code to its IRI (existing NCIt or minted op:MINT-*)."""
    if code.startswith("MINT-"):
        return f"<{vocab.ONTOPRISM_NS}{code}>"
    return f"<{NCIT_NS}{code}>"


def _axis_uri(axis: str) -> str:
    """Map an axis identifier to its IRI."""
    if axis.startswith("op:"):
        return f"<{vocab.ONTOPRISM_NS}{axis[3:]}>"
    return f"<{NCIT_NS}{axis}>"


def _p(predicate_iri: str) -> str:
    """Bracket a vocabulary predicate IRI for embedding as a Turtle term."""
    return f"<{predicate_iri}>"


def _emit_equivalence(subj: str, dec: Decomposition) -> str | None:
    """Build Turtle for ``owl:equivalentClass`` intersection axiom, or ``None``
    if there is no genus or no role-sourced constituents to reconstruct."""
    if dec.genus_code is None:
        return None

    genus_iri = f"<{NCIT_NS}{dec.genus_code}>"
    restrictions: list[str] = []
    for c in dec.constituents:
        if c.axis_source != "role":
            continue
        property_iri = _axis_uri(c.axis)
        filler_iri = _filler_iri(c.filler_code)
        restrictions.append(
            f"        [ a <{OWL_NS}Restriction> ;\n"
            f"          <{OWL_NS}onProperty> {property_iri} ;\n"
            f"          <{OWL_NS}someValuesFrom> {filler_iri} ]"
        )
    if not restrictions:
        return None

    return (
        f"{subj} <{OWL_NS}equivalentClass> [\n"
        f"    a <{OWL_NS}Class> ;\n"
        f"    <{OWL_NS}intersectionOf> (\n"
        f"        {genus_iri}\n"
        f"{chr(10).join(restrictions)}\n"
        f"    )\n"
        f"] ."
    )


def _render_one(
    dec: Decomposition,
    *,
    run_id: str = "",
    emitted_on: date,
    emit_equivalence: bool = False,
) -> list[str]:
    """Render Turtle triples for a single *dec* into a list of statement strings."""
    subj = f"<{NCIT_NS}{dec.code}>"
    lines: list[str] = []

    if emit_equivalence:
        eq = _emit_equivalence(subj, dec)
        if eq is not None:
            lines.append(eq)

    lines.append(
        f'{subj} {_p(vocab.REPRESENTATION_STATUS)} "{vocab.LEGACY_PRECOORDINATED}" ;',
    )
    lines.append(
        f"   {_p(vocab.DECOMPOSED_ON)}"
        f' "{emitted_on}"^^<http://www.w3.org/2001/XMLSchema#date> .',
    )

    if run_id:
        lines.append(f'{subj} {_p(vocab.DECOMPOSED_BY)} "{run_id}" .')

    for c in dec.constituents:
        filler = _filler_iri(c.filler_code)
        auri = _axis_uri(c.axis)
        const = (
            f"   [{_p(vocab.AXIS)} {auri} ; "
            f"{_p(vocab.FILLER)} {filler} ; "
            f'{_p(vocab.AXIS_SOURCE)} "{c.axis_source}"'
        )
        if c.most_specific:
            const += f" ; {_p(vocab.MOST_SPECIFIC)} true"
        lines.append(f"{subj} {_p(vocab.HAS_CONSTITUENT)}{const} ] .")

    return lines


async def write_ttl(
    decompositions: Iterable[Decomposition],
    dest: Path | None = None,
    *,
    run_id: str = "",
    emitted_on: date | None = None,
    emit_equivalence: bool = False,
) -> Path | None:
    """Render all *decompositions* as Turtle triples into *dest* (or stdout).

    Writes additively — no deletes, no other graph targeted.  Returns the written path
    or ``None`` when writing to stdout.
    """
    if emitted_on is None:
        emitted_on = date.today()
    buf: list[str] = []

    for dec in decompositions:
        buf.extend(
            _render_one(
                dec,
                run_id=run_id,
                emitted_on=emitted_on,
                emit_equivalence=emit_equivalence,
            )
        )

    ttl = "\n".join(buf) + "\n"

    if dest is None:
        sys.stdout.write(ttl)
        return None

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(ttl, encoding="utf-8")
    return dest
