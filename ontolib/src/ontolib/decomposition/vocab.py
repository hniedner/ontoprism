"""The ontoprism decomposition vocabulary (the additive ``op:`` contract).

Single source of truth for the named graph and predicate IRIs the decomposition engine
writes (#4) and the read API/UI (#9) consume. See the engine design doc (§4.1-§4.2,
§14 decision 3).
"""

from __future__ import annotations

# Persistent w3id identifier (design §14 decision 3) — need not resolve to be a valid
# namespace, but is community-standard and controllable via a one-line redirect PR.
ONTOPRISM_NS = "https://w3id.org/ontoprism/vocab#"

# All engine output goes to this named graph, kept separate from both the inferred
# default graph and the stated input graph — additive, never mutating the source.
DECOMPOSED_GRAPH_IRI = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus-decomposed.owl"

# Value of ``op:representationStatus`` flagging a decomposed source concept.
LEGACY_PRECOORDINATED = "legacy-precoordinated"

# --- op: predicate IRIs -----------------------------------------------------------
REPRESENTATION_STATUS = f"{ONTOPRISM_NS}representationStatus"
DECOMPOSED_ON = f"{ONTOPRISM_NS}decomposedOn"
DECOMPOSED_BY = f"{ONTOPRISM_NS}decomposedBy"
HAS_CONSTITUENT = f"{ONTOPRISM_NS}hasConstituent"
AXIS = f"{ONTOPRISM_NS}axis"
FILLER = f"{ONTOPRISM_NS}filler"
AXIS_SOURCE = f"{ONTOPRISM_NS}axisSource"
MOST_SPECIFIC = f"{ONTOPRISM_NS}mostSpecific"

# Regimen (mereological) kind — the #4 regimen mini-design.
HAS_COMPONENT = f"{ONTOPRISM_NS}hasComponent"
DECOMPOSITION_KIND = f"{ONTOPRISM_NS}decompositionKind"
