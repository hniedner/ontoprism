"""Constants for the external-ontology xref layer (design §8.1-8.2)."""

from __future__ import annotations

from typing import Literal

# Base IRI for immutable, source-specific generation graphs. Never write mappings to
# the stated NCIt graph or the decomposed graph.
NCIT_UPSTREAM_XREF_GRAPH_IRI = (
    "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus-upstream-xref.owl"
)

# SSSOM predicate vocabulary (SKOS mapping properties — ANNOTATION ONLY,
# never fed to a reasoner).
SKOS_NS = "http://www.w3.org/2004/02/skos/core#"
EXACT_MATCH: Literal["http://www.w3.org/2004/02/skos/core#exactMatch"] = (
    "http://www.w3.org/2004/02/skos/core#exactMatch"
)
CLOSE_MATCH: Literal["http://www.w3.org/2004/02/skos/core#closeMatch"] = (
    "http://www.w3.org/2004/02/skos/core#closeMatch"
)
BROAD_MATCH: Literal["http://www.w3.org/2004/02/skos/core#broadMatch"] = (
    "http://www.w3.org/2004/02/skos/core#broadMatch"
)
NARROW_MATCH: Literal["http://www.w3.org/2004/02/skos/core#narrowMatch"] = (
    "http://www.w3.org/2004/02/skos/core#narrowMatch"
)
RELATED_MATCH: Literal["http://www.w3.org/2004/02/skos/core#relatedMatch"] = (
    "http://www.w3.org/2004/02/skos/core#relatedMatch"
)

type MappingPredicate = Literal[
    "http://www.w3.org/2004/02/skos/core#exactMatch",
    "http://www.w3.org/2004/02/skos/core#closeMatch",
    "http://www.w3.org/2004/02/skos/core#broadMatch",
    "http://www.w3.org/2004/02/skos/core#narrowMatch",
    "http://www.w3.org/2004/02/skos/core#relatedMatch",
]

ALLOWED_PREDICATES = frozenset(
    {EXACT_MATCH, CLOSE_MATCH, BROAD_MATCH, NARROW_MATCH, RELATED_MATCH}
)

# SSSOM `mapping_justification` values — the process that produced a candidate.
#
# `LEXICAL_MATCHING` and `COMPOSITE_MATCHING` are published semapv terms
# (`mapping-commons/semantic-mapping-vocabulary`, `semapv-terms.tsv`).
# A publisher database cross-reference is represented by an explicit project-local
# process identifier because SEMAPV does not publish that term.
LEXICAL_MATCHING = "semapv:LexicalMatching"
DATABASE_CROSS_REFERENCE = "https://ontoprism.org/vocab#PublisherDatabaseCrossReference"
# Both passes independently produced the same pair (D34): the upstream class xrefs the
# NCIt code AND the two labels agree. semapv defines this as "a matching process based
# on multiple matching processes" — which is exactly the claim, and the reason the pair
# can carry two independent signals without either justifying itself.
COMPOSITE_MATCHING = "semapv:CompositeMatching"

# Lifecycle states (D29). Starts 'proposed'; promoted by curation/validation.
LIFECYCLE_STATES = frozenset(
    {"proposed", "validated", "active", "quarantined", "retired"}
)
type MappingLifecycle = Literal[
    "proposed", "validated", "active", "quarantined", "retired"
]
