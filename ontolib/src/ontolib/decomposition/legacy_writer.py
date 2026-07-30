"""Write additive decomposition triples to a TTL file (design §8).

Pure function: takes decompositions and writes RDF/Turtle to stdout or a file path.
Emits plain, graph-agnostic Turtle triples — it has no concept of "which named graph"
and never emits a ``DELETE``; the caller loads the output into ``DECOMPOSED_GRAPH_IRI``
(see ``scripts/decompose.py``'s ``client.load(..., graph_iri=...)``). The source graphs
are never referenced in the output at all.  Uses the op: vocabulary from
:mod:`ontolib.decomposition.vocab`.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from ontolib.decomposition import vocab
from ontolib.decomposition.axis_contracts import AXIS_CONTRACTS, AxisContract
from ontolib.decomposition.models import GenusDefinitionFact
from ontolib.terminologies.namespaces import NCIT_NS

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ontolib.decomposition.models import Constituent, Decomposition, DefinitionFact


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


def _render_constituent(subj: str, constituent: Constituent) -> str:
    filler = _filler_iri(constituent.filler_code)
    auri = _axis_uri(constituent.axis)
    rendered = (
        f"   [{_p(vocab.AXIS)} {auri} ; "
        f"{_p(vocab.FILLER)} {filler} ; "
        f'{_p(vocab.AXIS_SOURCE)} "{constituent.axis_source}"'
    )
    if constituent.source_role is not None:
        rendered += f" ; {_p(vocab.SOURCE_ROLE)} <{NCIT_NS}{constituent.source_role}>"
    if constituent.most_specific:
        rendered += f" ; {_p(vocab.MOST_SPECIFIC)} true"
    if constituent.group is not None:
        rendered += f' ; {_p(vocab.GROUP)} "{constituent.group}"'
    if constituent.needs_review:
        rendered += f" ; {_p(vocab.NEEDS_REVIEW)} true"
    for source_id in constituent.source_definition_ids:
        rendered += (
            f" ; {_p(vocab.SOURCE_DEFINITION_FACT)} "
            f"<{vocab.DEFINITION_FACT_NS}{source_id}>"
        )
    return f"{subj} {_p(vocab.HAS_CONSTITUENT)}{rendered} ] ."


def _render_axis_contract(contract: AxisContract) -> list[str]:
    axis = _axis_uri(contract.axis)
    owl_object_property = "<http://www.w3.org/2002/07/owl#ObjectProperty>"
    rdfs = "http://www.w3.org/2000/01/rdf-schema#"
    lines = [
        f"{axis} <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> "
        f"{owl_object_property} .",
        f"{axis} <{rdfs}label> {json.dumps(contract.label)} .",
        f"{axis} <{rdfs}comment> {json.dumps(contract.definition)} .",
        f"{axis} <{rdfs}domain> <{NCIT_NS}{contract.domain_code}> .",
        f"{axis} <{rdfs}range> <{NCIT_NS}{contract.range_code}> .",
    ]
    lines.extend(
        f"{axis} {_p(vocab.NORMALIZED_FROM_ROLE)} <{NCIT_NS}{role}> ."
        for role in contract.source_roles
    )
    lines.extend(
        f"{axis} {_p(vocab.CONTRACT_PROVENANCE)} {json.dumps(source)} ."
        for source in contract.provenance
    )
    return lines


def _render_axis_contracts() -> list[str]:
    return [
        line
        for axis in sorted(AXIS_CONTRACTS)
        for line in _render_axis_contract(AXIS_CONTRACTS[axis])
    ]


def _render_definition_fact(subj: str, fact: DefinitionFact) -> list[str]:
    fact_iri = f"<{vocab.DEFINITION_FACT_NS}{fact.fact_id}>"
    fact_kind = "genus" if isinstance(fact, GenusDefinitionFact) else "restriction"
    lines = [
        f"{subj} {_p(vocab.HAS_DEFINITION_FACT)} {fact_iri} .",
        f'{fact_iri} {_p(vocab.FACT_KIND)} "{fact_kind}" .',
        f"{fact_iri} {_p(vocab.ANCHOR)} <{NCIT_NS}{fact.anchor_code}> .",
        f'{fact_iri} {_p(vocab.DEFINITION_GROUP)} "{fact.group_id}" .',
        f"{fact_iri} {_p(vocab.DEFINITION_DEPTH)} {fact.depth} .",
    ]
    if isinstance(fact, GenusDefinitionFact):
        lines.extend(
            (
                f"{fact_iri} {_p(vocab.GENUS)} <{NCIT_NS}{fact.genus_code}> .",
                f"{fact_iri} {_p(vocab.IS_DEFINED)} {str(fact.is_defined).lower()} .",
            )
        )
        return lines
    lines.extend(
        (
            f"{fact_iri} {_p(vocab.DEFINITION_ROLE)} <{NCIT_NS}{fact.role_code}> .",
            f"{fact_iri} {_p(vocab.FILLER)} <{NCIT_NS}{fact.filler_code}> .",
        )
    )
    return lines


def _render_complete_definition(subj: str, dec: Decomposition) -> list[str]:
    complete = dec.complete_definition
    if complete is None:
        return []
    lines = [
        f'{subj} {_p(vocab.COMPLETE_DEFINITION_IDENTITY)} "{complete.identity}" .',
        f"{subj} {_p(vocab.COMPLETE_FACT_COUNT)} {dec.complete_fact_count} .",
        f"{subj} {_p(vocab.PROJECTED_FACT_COUNT)} {dec.projected_fact_count} .",
        f"{subj} {_p(vocab.PROJECTION_LOSS_COUNT)} {dec.projection_loss_count} .",
    ]
    for fact in complete.facts:
        lines.extend(_render_definition_fact(subj, fact))
    return lines


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

    lines.extend(_render_constituent(subj, c) for c in dec.constituents)
    lines.extend(_render_complete_definition(subj, dec))
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
            "equivalence emission is not available without a separately validated "
            "proof-bearing export mode"
        )
    if emitted_on is None:
        emitted_on = date.today()
    buf = _render_axis_contracts()

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
