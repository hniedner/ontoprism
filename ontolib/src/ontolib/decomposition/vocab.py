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

# --- Reserved: not yet emitted by any writer (DECISIONS D19/D20) -------------------
# Declared here so the design doc's vocabulary table and the golden set have a single
# source of truth for these IRIs. The extractor that produces them is issue #44's
# graduation step; `Constituent` has no group field yet.

# D19: relationship-group id. Co-equal, non-nested fillers of one concept are grouped
# rather than collapsed, keeping the complete representation lossless/round-trippable.
GROUP = f"{ONTOPRISM_NS}group"

# D20, refinement 1: a primary-site restriction anchored on a lineage/histology-generic
# genus (e.g. via C3010 "Endocrine Neoplasm") is routed here instead of NCIt's R101.
ASSOCIATED_LINEAGE_CLASSIFICATION = f"{ONTOPRISM_NS}associatedLineageClassification"

# D20, refinement 2: the co-present region/tissue filler of a residual, non-lineage
# primary-site tie; the organ-level filler stays on R101.
ASSOCIATED_REGION = f"{ONTOPRISM_NS}associatedRegion"
