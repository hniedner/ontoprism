"""Constants for the external-ontology xref layer (design §8.1-8.2)."""

from __future__ import annotations

# Single additive named graph that holds all NCIt<->upstream mapping triples.
# Never write mappings to the stated NCIt graph or the decomposed graph.
NCIT_UPSTREAM_XREF_GRAPH_IRI = (
    "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus-upstream-xref.owl"
)

# SSSOM predicate vocabulary (SKOS mapping properties — ANNOTATION ONLY,
# never fed to a reasoner).
SKOS_NS = "http://www.w3.org/2004/02/skos/core#"
EXACT_MATCH = f"{SKOS_NS}exactMatch"
CLOSE_MATCH = f"{SKOS_NS}closeMatch"
BROAD_MATCH = f"{SKOS_NS}broadMatch"
NARROW_MATCH = f"{SKOS_NS}narrowMatch"
RELATED_MATCH = f"{SKOS_NS}relatedMatch"

ALLOWED_PREDICATES = frozenset(
    {EXACT_MATCH, CLOSE_MATCH, BROAD_MATCH, NARROW_MATCH, RELATED_MATCH}
)

# SSSOM `mapping_justification` values — the process that produced a candidate.
#
# `LEXICAL_MATCHING` and `COMPOSITE_MATCHING` are published semapv terms
# (`mapping-commons/semantic-mapping-vocabulary`, `semapv-terms.tsv`).
# `DATABASE_CROSS_REFERENCE` is NOT: semapv has no term for "an upstream database
# asserts a cross-reference", and this one predates the vocabulary check. It is kept
# because it is the string persisted in `concept_xref.mapping_justification` on every
# ingested row; renaming it is a data migration, not a constant edit.
LEXICAL_MATCHING = "semapv:LexicalMatching"
DATABASE_CROSS_REFERENCE = "semapv:DatabaseCrossReference"
# Both passes independently produced the same pair (D34): the upstream class xrefs the
# NCIt code AND the two labels agree. semapv defines this as "a matching process based
# on multiple matching processes" — which is exactly the claim, and the reason the pair
# can carry two independent signals without either justifying itself.
COMPOSITE_MATCHING = "semapv:CompositeMatching"

# Lifecycle states (D29). Starts 'proposed'; promoted by curation/validation.
LIFECYCLE_STATES = frozenset(
    {"proposed", "validated", "active", "quarantined", "retired"}
)
