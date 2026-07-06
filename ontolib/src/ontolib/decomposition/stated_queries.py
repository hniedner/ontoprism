"""SPARQL builders that read the **stated** NCIt named graph (design §3, §2).

Extraction must run off the stated (asserted) OWL, never the inferred default graph,
to avoid ancestor-closure bleed and the ``Excludes_*`` negative axioms (assessment §4).
These builders reuse the restriction-traversal pattern from ``role_queries.py`` wrapped
in a ``GRAPH <STATED_GRAPH_IRI>`` clause, and reuse ``safe_iri`` for injection safety.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ontolib.terminologies.namespaces import NCIT_NS, OWL_NS, RDFS_NS
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI
from ontolib.terminologies.ncit.property_codes import SEMANTIC_TYPE
from ontolib.terminologies.oxigraph_http_client import safe_iri

if TYPE_CHECKING:
    from collections.abc import Iterable

_PREFIXES = f"""
        PREFIX rdfs: <{RDFS_NS}>
        PREFIX owl: <{OWL_NS}>
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
