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

# Lifecycle states (D29). Starts 'proposed'; promoted by curation/validation.
LIFECYCLE_STATES = frozenset(
    {"proposed", "validated", "active", "quarantined", "retired"}
)
