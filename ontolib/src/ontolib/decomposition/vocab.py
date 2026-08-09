"""The ontoprism decomposition vocabulary (the additive ``op:`` contract).

Single source of truth for the named graph and predicate IRIs the decomposition engine
writes (#4) and the read API/UI (#9) consume. See the engine design doc (§4.1-§4.2,
§14 decision 3).
"""

from __future__ import annotations

# Persistent w3id identifier (design §14 decision 3) — need not resolve to be a valid
# namespace, but is community-standard and controllable via a one-line redirect PR.
ONTOPRISM_NS = "https://w3id.org/ontoprism/vocab#"
DEFINITION_FACT_NS = "https://w3id.org/ontoprism/decomposition/fact/"
DEFINITION_GROUP_NS = "https://w3id.org/ontoprism/decomposition/group/"

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
NEEDS_REVIEW = f"{ONTOPRISM_NS}needsReview"
SOURCE_DEFINITION_FACT = f"{ONTOPRISM_NS}sourceDefinitionFact"
SOURCE_ROLE = f"{ONTOPRISM_NS}sourceRole"
NORMALIZED_FROM_ROLE = f"{ONTOPRISM_NS}normalizedFromRole"
CONTRACT_PROVENANCE = f"{ONTOPRISM_NS}contractProvenance"
AXIS_MODALITY = f"{ONTOPRISM_NS}axisModality"
GOVERNANCE_STATUS = f"{ONTOPRISM_NS}governanceStatus"
GOVERNANCE_SINCE = f"{ONTOPRISM_NS}governanceSince"
GOVERNANCE_REVIEW_BY = f"{ONTOPRISM_NS}governanceReviewBy"
GOVERNANCE_REVIEW_TRIGGER = f"{ONTOPRISM_NS}governanceReviewTrigger"
GOVERNANCE_FALLBACK_AXIS = f"{ONTOPRISM_NS}governanceFallbackAxis"
GOVERNANCE_FALLBACK_NEEDS_REVIEW = f"{ONTOPRISM_NS}governanceFallbackNeedsReview"
GOVERNANCE_EVIDENCE_COUNT = f"{ONTOPRISM_NS}governanceEvidenceCount"
PUBLICATION_MARKER = f"{ONTOPRISM_NS}decompositionPublication"
PUBLICATION_CLASS = f"{ONTOPRISM_NS}DecompositionPublication"
PUBLICATION_RUN = f"{ONTOPRISM_NS}publicationRun"
PUBLICATION_SOURCE_IDENTITY = f"{ONTOPRISM_NS}publicationSourceIdentity"
PUBLICATION_REPRESENTATION_IDENTITY = f"{ONTOPRISM_NS}publicationRepresentationIdentity"
PUBLICATION_BUILT_AT = f"{ONTOPRISM_NS}publicationBuiltAt"

# --- Complete stated definition ---------------------------------------------------
HAS_DEFINITION_FACT = f"{ONTOPRISM_NS}hasDefinitionFact"
HAS_DEFINITION_GROUP = f"{ONTOPRISM_NS}hasDefinitionGroup"
HAS_ROOT_DEFINITION_GROUP = f"{ONTOPRISM_NS}hasRootDefinitionGroup"
HAS_CHILD_DEFINITION_GROUP = f"{ONTOPRISM_NS}hasChildDefinitionGroup"
COMPLETE_DEFINITION_IDENTITY = f"{ONTOPRISM_NS}completeDefinitionIdentity"
COMPLETE_FACT_COUNT = f"{ONTOPRISM_NS}completeFactCount"
PROJECTED_FACT_COUNT = f"{ONTOPRISM_NS}projectedFactCount"
PROJECTION_LOSS_COUNT = f"{ONTOPRISM_NS}projectionLossCount"
FACT_KIND = f"{ONTOPRISM_NS}factKind"
ANCHOR = f"{ONTOPRISM_NS}anchor"
DEFINITION_GROUP = f"{ONTOPRISM_NS}definitionGroup"
DEFINITION_DEPTH = f"{ONTOPRISM_NS}definitionDepth"
GENUS = f"{ONTOPRISM_NS}genus"
IS_DEFINED = f"{ONTOPRISM_NS}isDefined"
DEFINITION_ROLE = f"{ONTOPRISM_NS}role"

# Regimen (mereological) kind — the #4 regimen mini-design.
HAS_COMPONENT = f"{ONTOPRISM_NS}hasComponent"
DECOMPOSITION_KIND = f"{ONTOPRISM_NS}decompositionKind"

# --- Projection vocabulary (DECISIONS D19/D20) -------------------------------------
# The writer serializes these values when a constituent supplies them.

# D19: relationship-group id for co-equal, non-nested fillers of one concept.
GROUP = f"{ONTOPRISM_NS}group"

# D20, refinement 1: a primary-site restriction anchored on a lineage/histology-generic
# genus (e.g. via C3010 "Endocrine Neoplasm") is routed here from NCIt's R101.
ASSOCIATED_LINEAGE_CLASSIFICATION = f"{ONTOPRISM_NS}associatedLineageClassification"

# D20, refinement 2: the co-present region/tissue filler of a residual, non-lineage
# primary-site tie; the organ-level filler routes to op:PrimarySite.
ASSOCIATED_REGION = f"{ONTOPRISM_NS}associatedRegion"
