# Ontology platform and enhanced NCIt product boundary

**Status:** Target architecture, not current implementation · **Decision:** D86 ·
**Date:** 2026-09-04

This design separates OntoPrism's implemented product from its intended platform. Normative
language under **Target architecture** describes delivery contracts for future work; it is not
evidence that those capabilities exist.

## Current implementation

OntoPrism is NCIt-centered. It implements certified local repository reads and adapters for
NCIt and related repositories, browser exploration and visualization, NCIt curation and
decomposition surfaces, typed API access, and selected alignment services. The repository has
NCIt-specific library, API, and frontend paths (`git ls-files
ontolib/src/ontolib/terminologies/ncit backend/src/backend/api
frontend/src/routes/repositories/ncit`, 2026-09-04).

No generic ontology-adapter type/system, generic editing or reasoning implementation, generic AI
authoring, or permanent release-forward reconciliation currently ships (`git grep -n
OntologyAdapter -- ontolib/src backend/src frontend/src`, which returned no matches,
2026-09-04). Graph/storage separation
protects the official stated NCIt plane from overlay mutation: the source and additive
decomposition graphs have distinct constants and the decomposition reader targets only its
additive graph (`git grep -n STATED_GRAPH_IRI --
ontolib/src/ontolib/terminologies/ncit/owl_load.py
ontolib/src/ontolib/decomposition/read_queries.py` and `git grep -n DECOMPOSED_GRAPH_IRI --
ontolib/src/ontolib/decomposition/vocab.py ontolib/src/ontolib/decomposition/read_queries.py`,
2026-09-04). Current certification does not promise independently selectable source views or byte
recovery for every enhanced release.

## Target architecture

The target is an ontology-generic engineering and management architecture with three boundaries.
An ontology may participate only through the capabilities its adapter declares; a domain may
further restrict those capabilities through policy.

### Platform core

The target core owns reusable view/navigation, visualization, editing/authoring, reasoning,
validation, and release/version/provenance/alignment management workflows. It coordinates
capabilities but does not guess ontology-specific identity, semantics, authority, or release rules.

### Ontology adapters

An adapter declares which identity, release, read/query, edit, reason, validate, publish,
provenance, and alignment capabilities it supports. Unsupported capabilities remain explicit and
fail closed; the core must not infer them from a common storage technology. Adapter declarations
are part of the target contract—no generic adapter system exists today.

### Domain policy

Domain policy decides applicable validation, evidence, governance, licensing, and publication
rules without changing adapter facts. For NCIt, D60 governs emitted enhancement and D86 governs
the product/platform distinction. A different adapter does not inherit NCIt governance merely by
using the same core.

NCIt is the first and primary product, not an architectural limit. Its implementation should
become one composition of the generic boundaries rather than the implicit definition of them.

## Enhanced NCIt planes and narrow compatibility

The target enhanced release composes:

1. one identified official NCIt release source plane; and
2. separately identified and versioned OntoPrism overlays or derived views.

The official source and each overlay keep distinct identities. The target compatibility contract
is limited to **source containment**, source identifiers as **release-bound anchors**,
**source-view recoverability**, and explicit **provenance and view distinction**. A consumer can
name whether it requests the official source view or a particular enhanced composition.

Byte recovery requires retention of the original artifact and its digest. A reconstructed RDF
graph with matching semantics is not automatically the original byte stream. Current graph
separation protects the official stated plane from overlay mutation, but does not certify that the
source is independently selectable/recoverable or byte-recoverable for every enhanced release.

Compatibility does **not** mean conservative extension, logical equivalence, query equivalence,
identical hierarchy, search, or reasoner behavior, arbitrary drop-in substitution, D43
reversibility, or official endorsement. “Fully backward compatible” is therefore not a product
claim. Each stronger property requires its own evidence and contract.

Source containment applies only to the official source plane. The effective view may intentionally
differ. Best-effort migration and compatibility are reported for each named consumer profile and
dimension as preserved, changed, breaking, or unknown, with the denominator and known breaks;
there is no blanket compatibility Boolean. An enhanced export must not serialize edited semantics
under an official NCIt IRI. The distinct official source export remains available. Target byte
recovery is promised only when the original artifact and its digest were retained.

## Target effective corrections

This section is a target contract, not shipped functionality. Official source authority means
authoritative evidence of what NCI published, not a claim of scientific or logical infallibility.
The source plane is protected from overlay mutation and remains independently viewable where
current surfaces support it; this does not overclaim current source-view or byte recoverability.

An evidence-backed and accountably reviewed effective correction can add assertions, suppress exact
official source axioms from composition, replace them, or qualify them. Its eligible error class is
exactly one of source publication/representation error, empirical/scientific error, or
logical/ontological error. Modelling preference is not an error and cannot destructively suppress or
replace official content. These classes classify decisions; they do not create another lifecycle.

Suppression is **named effective-view composition subtraction before reasoning**. It never mutates or
deletes the official source, and annotation-only suppression or a contradictory or negating axiom
must not represent suppression. Re-reasoning the exact composition is mandatory. Any inconsistency,
unsupported targets, or missing targets refuse publication.

The suppressed axiom always remains retrievable in the official source view. It also always appears
in the target delta/impact view as `removed-from-effective`, never as deleted, not-found, or an empty
result. The delta binds:

- source release and canonical assertion identity;
- the correction evidence and accountable decision;
- any replacement or qualification;
- stated and finite-profile inferred before/after effects;
- the declared affected closure and boundary evidence; and
- dependent impacts.

That simultaneous visibility is the contract: removal from effective composition is not absence
from official source authority.

## Enhanced identity and release-bound crosswalks

In the target, every enhanced NCIt concept and role, including unchanged official renditions, has a stable OntoPrism-governed enhanced-NCIt code.
Every code has a release-bound crosswalk to the original official concept or role unless its provenance is `new`.
The crosswalk records entity kind and computed cardinality, whether the rendition is unchanged or its exact change set, and an outcome of edit, split, merge, replacement, suppression, qualification, or new.

OntoPrism does not invent official entity-level versions. Official identity is:
**official release + official concept/role code + canonical source entity/assertion fingerprints and profile**.
Enhanced identity is:
**enhanced code + immutable entity revision + enhanced release/overlay/composition identities**.
An enhanced code is never reused and is not replaced by NCI adoption.

Everything emitted is OntoPrism-governed enhanced NCIt content. Release-bound official NCIt
identifiers are source anchors and crosswalk endpoints, not necessarily the emitted enhanced primary
identifiers. Content remains derived from, aligned to, or corroborated by identified sources;
provenance does not transfer ownership. D86 qualifies D60 and does not supersede it.

## caDSR resolution and compatibility

caDSR source rows and anchors remain official NCIt codes; they are never rewritten. Enhanced
resolution derives through the release-bound crosswalk and reports exactly one typed outcome:
unique, split, merge, ambiguous, or unresolved.
Resolution must not heuristically select a split result, and ambiguous or unresolved outcomes cannot
look successful. Reports distinguish official-anchor coverage and enhanced-resolution coverage.

Compatibility is evaluated against a named consumer profile and dimension, with the result,
denominator and known breaks. Edited enhanced semantics are never published under an official NCIt
IRI. Official-source and enhanced exports remain distinct.

## Affected graph and dependent impacts

An affected-graph claim is complete only under an explicit versioned graph closure with its
relation, direction, bounds, and boundary witnesses.
Stated and inferred effects remain separate. Anything outside that declared graph boundary is not
silently enumerated as a graph member. Non-graph dependents are dependency-registry impacts, not
graph members, and receive an explicit outcome: stale-pending, recompute, revalidate, remap, or refuse.
[#262](https://github.com/hniedner/ontoprism/issues/262) owns exact impact types; this design does not
duplicate that vocabulary.

## Migration, reconciliation, and adoption

Every official-to-enhanced reference receives an explicit typed outcome; nothing remains
unclassified.
Split, merge, ambiguous, and unresolved outcomes cannot be presented as success. On every later
official release, every correction is reconciled against exact source and enhanced identities.
Nothing is silently replayed, dropped, or overridden.

[#316](https://github.com/hniedner/ontoprism/issues/316) currently owns proposal transfer. A
correction-aware extension needs explicit future ownership rather than being attributed to #316
today. NCI exact adoption can make an override redundant only with exact certified
release/assertion evidence.
Partial, ambiguous, or divergent adoption requires human review. No reconciliation step silently
replays, drops, or overrides a correction.

The stable enhanced code remains through gradual exact, partial, or ambiguous official adoption;
history is append-only, and a later official code may differ. Only exact certified
release/assertion evidence can support a #304 lifecycle transition, and reconciliation does not
assign lifecycle state.
[#304](https://github.com/hniedner/ontoprism/issues/304) remains the vocabulary owner.

The correction-aware target refuses automatic replay and publication for every unresolved,
unsupported, split, or ambiguous outcome. This design specifies that future boundary; it does not
claim an implementation or assign it to #316's current scope.

## Target correction views

The target visualization provides `official source`, `effective`, `delta`, `impact`, and `migration`
views bound to exact release, overlay, composition, and entity/assertion identities. A suppressed
official source axiom and its `removed-from-effective` delta are simultaneously inspectable. Edge
and axiom kinds remain typed and are never flattened to a generic relation.

## Governance and adoption

OntoPrism may be locally useful and locally published under accountable local governance without
NCI adoption. Adoption is optional and non-blocking. Only evidence in an identified certified
official NCIt release supports “official,” “NCI-authored,” or `accepted-in-ncit`; local approval,
submission, or local publication does not.

D86 qualifies but does not supersede D60. Everything OntoPrism emits as NCIt enhancement remains
OntoPrism-governed NCIt, including content derived from or aligned to another terminology. Exact
lifecycle vocabulary and current model divergence belong to
[#304](https://github.com/hniedner/ontoprism/issues/304); this design neither installs a new
lifecycle nor claims that existing variants have converged. OntoPrism must never assign approval,
submission, publication, or adoption to NCI.

## Metathesaurus interoperability capability

The target supports typed, evidence-bearing mappings and link-outs to independently identified
local or remote ontology releases. It does not claim that OntoPrism is NCI Metathesaurus. Each
record carries:

- **endpoint ontology, release, and identity**;
- **relation type and direction**;
- **evidence, provenance, and status**;
- **license** terms or access classification; and
- **remote availability, cache, and freshness** observations where remote material is used.

A mutable remote URL is a locator, not release identity. A link-out is neither an import nor a runtime dependency.
A shared CUI is not equivalence evidence. `exact`, `close`, `broad`, `narrow`, and domain relations
remain distinct instead of collapsing into a generic “mapped” relation.

Per D60, emitted NCIt enhancement is derived from, aligned to, or corroborated by those identified
records; their provenance does not transfer ownership and their content is not silently blended
into an emitted definition.

## Evidence-bound AI

AI-enhanced ontology management is a target for adapter-supported ontologies, not a current generic
authoring feature. An AI outcome is exactly one of: `candidate`, `abstain`, or `failure`. A candidate
binds its evidence and the model, tool, and source identities that produced it; abstention is not
failure, and failure is not an empty candidate.

Deterministic validation evaluates every machine-checkable claim. Model confidence is never
substituted for evidence. A **Human accountable authority** decides semantic publication under
domain policy. AI cannot approve, publish, submit, or adopt content, cannot assign NCI action, and
cannot turn model confidence into evidence.
