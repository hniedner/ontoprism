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
from ontolib.terminologies.namespaces import NCIT_NS

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


def _render_one(
    dec: Decomposition,
    *,
    run_id: str = "",
    emitted_on: date,
) -> list[str]:
    """Render Turtle triples for a single *dec* into a list of statement strings."""
    subj = f"<{NCIT_NS}{dec.code}>"
    lines: list[str] = []

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
        if c.group is not None:
            const += f' ; {_p(vocab.GROUP)} "{c.group}"'
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
    if emit_equivalence:
        raise ValueError(
            "equivalence emission is not available until a proof-bearing "
            "representation can establish exact completeness (#153)"
        )
    if emitted_on is None:
        emitted_on = date.today()
    buf: list[str] = []

    for dec in decompositions:
        buf.extend(
            _render_one(
                dec,
                run_id=run_id,
                emitted_on=emitted_on,
            )
        )

    ttl = "\n".join(buf) + "\n"

    if dest is None:
        sys.stdout.write(ttl)
        return None

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(ttl, encoding="utf-8")
    return dest
