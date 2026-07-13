"""The merged-EL validation ontology and the curated bridge axiom (design §4.4, D28).

The logical bridge between an NCIt class and its upstream counterpart is a **separate
curated ``owl:equivalentClass`` axiom** — never the ``skos:*Match`` annotation, which
has no logical semantics and would import every mapping error into the reasoner as an
axiom.  :func:`bridge_axiom` refuses to build the bridge out of a SKOS predicate, and
:func:`build_validation_ontology` emits nothing else that could carry one.

The ontology handed to ELK is deliberately *small* (MIREOT-style partial fragments,
Courtot 2011): the stated named-class taxonomy around both endpoints, the already-
validated anchor bridges, and — only when ``include_bridge`` — the candidate bridge
under test.  We never classify the whole 10M-triple graph, and we stay inside OWL 2 EL
by construction: class declarations, ``rdfs:subClassOf``, ``owl:equivalentClass``.

The two builds are the heart of the non-circularity argument (D28):

* ``include_bridge=False`` — what *structural corroboration* is computed over.  The
  candidate is absent, so the upstream object can only be inferred to sit under the
  upstream image of an NCIt ancestor via a **separate, already-trusted** anchor.
* ``include_bridge=True`` — what the EL profile + satisfiability gate runs over: does
  *adding* this bridge keep the merge in EL and satisfiable?
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ontolib.repositories.xref.ttl_writer import object_iri
from ontolib.repositories.xref.vocab import ALLOWED_PREDICATES
from ontolib.terminologies.namespaces import NCIT_NS

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ontolib.repositories.xref.models import SSSOMRecord

OWL_NS = "http://www.w3.org/2002/07/owl#"
RDFS_NS = "http://www.w3.org/2000/01/rdf-schema#"
OWL_EQUIVALENT_CLASS = f"{OWL_NS}equivalentClass"

_ONTOLOGY_IRI = "http://ontoprism.org/xref/validation"


def _ncit_iri(code: str) -> str:
    return f"{NCIT_NS}{code}"


def bridge_axiom(
    subject_iri: str,
    upstream_iri: str,
    *,
    property_iri: str = OWL_EQUIVALENT_CLASS,
) -> str:
    """Render the curated logical bridge as a single Turtle triple.

    Raises ``ValueError`` if *property_iri* is a SKOS mapping property: a mapping
    annotation may never be fed to the reasoner as an equivalence (D28).
    """
    if property_iri in ALLOWED_PREDICATES:
        raise ValueError(
            "a SKOS mapping annotation may never be the logical bridge (D28); "
            f"refusing to assert {property_iri} as an equivalence axiom"
        )
    return f"<{subject_iri}> <{property_iri}> <{upstream_iri}> ."


def build_validation_ontology(
    record: SSSOMRecord,
    *,
    ncit_edges: set[tuple[str, str]],
    upstream_edges: set[tuple[str, str]],
    anchors: Sequence[tuple[str, str]],
    include_bridge: bool,
) -> str:
    """Assemble the small merged ontology for *record*, as Turtle.

    Args:
        record: The candidate under test.
        ncit_edges: Stated ``(child_code, parent_code)`` NCIt named-class edges.
        upstream_edges: Stated ``(child_curie, parent_curie)`` upstream edges.
        anchors: Already-validated ``(ncit_code, upstream_curie)`` bridges.
        include_bridge: Assert the candidate's own ``owl:equivalentClass``.

    Raises:
        ValueError: if the candidate pair appears in *anchors* — a candidate may not
            be its own trusted anchor (that is the circularity D28 forbids).
        KeyError: if an upstream CURIE carries a prefix with no known base IRI.
    """
    candidate = (record.subject_id, record.object_id)
    if candidate in tuple(anchors):
        raise ValueError(
            f"candidate {candidate} may not be its own anchor: the evidence for a "
            "bridge can never be that same bridge (D28)"
        )

    subject_iri = _ncit_iri(record.subject_id)
    upstream_iri = object_iri(record.object_id)

    classes: set[str] = {subject_iri, upstream_iri}
    axioms: list[str] = []

    for child, parent in sorted(ncit_edges):
        child_iri, parent_iri = _ncit_iri(child), _ncit_iri(parent)
        classes |= {child_iri, parent_iri}
        axioms.append(f"<{child_iri}> <{RDFS_NS}subClassOf> <{parent_iri}> .")

    for child, parent in sorted(upstream_edges):
        child_iri, parent_iri = object_iri(child), object_iri(parent)
        classes |= {child_iri, parent_iri}
        axioms.append(f"<{child_iri}> <{RDFS_NS}subClassOf> <{parent_iri}> .")

    for code, curie in anchors:
        anchor_ncit, anchor_upstream = _ncit_iri(code), object_iri(curie)
        classes |= {anchor_ncit, anchor_upstream}
        axioms.append(bridge_axiom(anchor_ncit, anchor_upstream))

    if include_bridge:
        axioms.append(bridge_axiom(subject_iri, upstream_iri))

    declarations = [f"<{iri}> a <{OWL_NS}Class> ." for iri in sorted(classes)]
    header = f"<{_ONTOLOGY_IRI}> a <{OWL_NS}Ontology> ."
    return "\n".join([header, *declarations, *axioms]) + "\n"
