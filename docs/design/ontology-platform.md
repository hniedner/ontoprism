# Ontology platform and enhanced NCIt product boundary

**Status:** Target architecture, not current implementation · **Decision:** D86 ·
**Date:** 2026-09-04

This design separates OntoPrism's implemented product from its intended platform. Normative
language under **Target architecture** describes delivery contracts for future work; it is not
evidence that those capabilities exist.

## Current implementation

OntoPrism is NCIt-centered. It implements certified local repository reads for
NCIt and related repositories, browser exploration and visualization, NCIt curation and
decomposition surfaces, typed API access, and selected alignment services. The repository has
NCIt-specific library, API, and frontend paths (`git ls-files
ontolib/src/ontolib/terminologies/ncit backend/src/backend/api
frontend/src/routes/repositories/ncit`, 2026-09-04).

No generic ontology-adapter type/system, generic editing or reasoning implementation, generic AI
authoring, correction system, or permanent release-forward reconciliation currently ships (`git grep -n
OntologyAdapter -- ontolib/src backend/src frontend/src`, which returned no matches,
2026-09-04). Current extraction and analysis read the official stated NCIt source (`git grep -n
STATED_GRAPH_IRI -- ontolib/src/ontolib/decomposition`, expected output: stated-graph query clauses
in `stated_queries.py`, `scope.py`, `walker.py`, `complete_definition.py`, and diagnostic/review
modules, 2026-09-04). The specific additive projection reader is different: `read_queries.py`
contains no `STATED_GRAPH_IRI` reference and targets `DECOMPOSED_GRAPH_IRI` (`git grep -n
STATED_GRAPH_IRI -- ontolib/src/ontolib/decomposition/read_queries.py` and `git grep -n
DECOMPOSED_GRAPH_IRI -- ontolib/src/ontolib/decomposition/read_queries.py`, expected output: no
result from the first command and the decomposed-graph query from the second, 2026-09-04).

Graph/storage separation protects the official stated NCIt plane through write isolation, not by
forbidding source reads. Current overlay publication writes use decomposed, upstream-xref,
enhanced-showcase, and scoped staging/generation graph IRIs. Among the inspected writer and
vocabulary paths, the stated constant occurs only in `owl_load.py` and in the import and valid
source-release `SELECT` in `enhanced_showcase.py` (`git grep -n STATED_GRAPH_IRI --
ontolib/src/ontolib/decomposition/publication.py
ontolib/src/ontolib/decomposition/enhanced_showcase.py
ontolib/src/ontolib/decomposition/legacy_writer.py
ontolib/src/ontolib/repositories/xref/publication.py
ontolib/src/ontolib/repositories/xref/ttl_writer.py
ontolib/src/ontolib/decomposition/vocab.py
ontolib/src/ontolib/repositories/xref/vocab.py
ontolib/src/ontolib/terminologies/ncit/owl_load.py`, expected output: the definition, import, and
`SELECT`, 2026-09-04). The write targets and derivations are shown by `git grep -n DECOMPOSED_GRAPH_IRI --
ontolib/src/ontolib/decomposition/publication.py
ontolib/src/ontolib/decomposition/enhanced_showcase.py
ontolib/src/ontolib/decomposition/legacy_writer.py ontolib/src/ontolib/decomposition/vocab.py`,
`git grep -n SHOWCASE_GRAPH_IRI -- ontolib/src/ontolib/decomposition/enhanced_showcase.py`, and
`git grep -n NCIT_UPSTREAM_XREF_GRAPH_IRI --
ontolib/src/ontolib/repositories/xref/publication.py
ontolib/src/ontolib/repositories/xref/ttl_writer.py
ontolib/src/ontolib/repositories/xref/vocab.py` (expected output: decomposed/showcase staging and
replacement targets plus upstream-xref generation/active derivations, 2026-09-04).

The current OntoPrism-authored NCI-domain graph IRIs—including the decomposition, upstream-xref,
and enhanced-showcase graphs—are technical debt and must not be represented as an official NCI
identifier. Their exact current values and showcase derivation are shown by `git grep -n
DECOMPOSED_GRAPH_IRI -- ontolib/src/ontolib/decomposition/vocab.py`, `git grep -n -A 2
NCIT_UPSTREAM_XREF_GRAPH_IRI -- ontolib/src/ontolib/repositories/xref/vocab.py`, and `git grep -n
SHOWCASE_GRAPH_IRI -- ontolib/src/ontolib/decomposition/enhanced_showcase.py` (expected output: the
two literal graph IRIs and the showcase IRI derived beneath the decomposed IRI, 2026-09-04). Every
future enhanced export must use an OntoPrism-governed enhanced namespace; a future implementation
issue must own that collective namespace change before export delivery. Current certification does
not promise independently selectable source views or byte recovery for every enhanced release.

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
is limited to **source containment** as immutable official-source preservation, source identifiers as **release-bound anchors**,
**source-view recoverability**, and explicit **provenance and view distinction**. A consumer can
name whether it requests the official source view or a particular enhanced composition.

Byte recovery requires retention of the original artifact and its digest. A reconstructed RDF
graph with matching semantics is not automatically the original byte stream. Current graph
separation protects the official stated plane from overlay mutation, but does not certify that the
source is independently selectable/recoverable or byte-recoverable for every enhanced release.

Compatibility does **not** mean conservative extension, logical equivalence, query equivalence,
identical hierarchy, search, or reasoner behavior, arbitrary drop-in substitution, D43
reversibility, or official endorsement. The target contract therefore rejects “fully backward
compatible” as a product claim. Each stronger property requires its own evidence and contract.

Source containment applies only to immutable official-source preservation. The effective enhanced
view intentionally need not contain official assertions. Best-effort migration and compatibility
are reported for each named consumer profile and dimension. `preserved`, `changed`, and `breaking`
require a tested denominator of at least one and evidence; `changed` identifies its differences and
`breaking` has a nonempty known-break set;
`unknown` carries a blocker or reason and cannot carry a success denominator. Overall compatibility
cannot be compatible when any required result is `unknown`;
there is no blanket compatibility Boolean. An enhanced export must not serialize edited semantics
under an official NCIt IRI. The source export profile remains distinct and the official source
export remains available. Target byte
recovery is promised only when the original artifact and its digest were retained.

## Target effective corrections

This section is a target contract, not shipped functionality. Official source authority means
authoritative evidence of what NCI published, not a claim of scientific or logical infallibility.
The target source plane is protected from overlay mutation and independently viewable. This target
does not overclaim current source-view or byte recoverability.

`OverlayIntent` is exactly `correction`, `enrichment`, or `modelling-alternative`. A correction has
exactly one error class: source publication/representation error, empirical/scientific error, or
logical/ontological error. Enrichment requires no error class. A modelling alternative is not a
correction error and may add or qualify only; it cannot suppress or replace official content. These
classes classify decisions; they do not create another lifecycle.

```text
OverlayIntent =
  correction { errorClass: exactly one of the three error classes }
  | enrichment
  | modelling-alternative { permittedOperations: add | qualify }
```

Suppression is **named effective-view composition subtraction before reasoning**. It never mutates or
deletes the official source, and annotation-only suppression or a contradictory or negating axiom
must not represent suppression. Re-reasoning the exact composition is mandatory. Any inconsistency,
unsupported targets, or missing targets refuse publication.

The suppressed canonical axiom always remains retrievable in the official source view. It also
always appears in the target delta/impact view as a nonempty `removed-from-effective` record, never
as deleted, absent, not-found, or an empty result. The delta binds:

- source release and canonical assertion identity;
- the correction evidence and accountable decision;
- any replacement or qualification;
- stated and finite-profile inferred before/after effects;
- the declared affected closure and boundary evidence; and
- dependent impacts.

That simultaneous visibility is the contract: removal from effective composition is not absence
from official source authority.

## Enhanced identity and release-bound crosswalks

`EnhancedEntityOrigin` is a discriminated union:

- `DerivedFromOfficial` has required release-bound official entity references; and
- `NewEnhancedEntity` forbids official entity references.

That shape makes an inconsistent optional source mapping unrepresentable. An unchanged rendition is
representable as derived. `EntityCrosswalkOutcome` is exactly `unchanged`, `edited`, `split`,
`merge`, `replacement`, or `new`; cardinality is derived from endpoint sets and is never stored
unchecked:

```text
EnhancedEntityOrigin =
  DerivedFromOfficial { officialEntityRefs: NonEmptySet<ReleaseBoundOfficialEntityRef> }
  | NewEnhancedEntity { officialEntityRefs: Absent }
```

| Outcome | Official → enhanced arity |
|---|---|
| `new` | 0 → 1 |
| `unchanged`, `edited`, and `replacement` | 1 → 1 |
| `split` | 1 → N, N ≥ 2 |
| `merge` | M → 1, M ≥ 2 |

`unchanged` is valid if and only if one official endpoint maps to one enhanced endpoint and the exact
change set is empty. A many-to-many case cannot inhabit `EntityCrosswalkOutcome`; if required, it is
routed to a distinct `complex-restructure` requiring human review rather than overloaded onto split
or merge. Qualification is an assertion operation, not necessarily an entity-crosswalk change.

The routing artifact has an explicit type home:

```text
CrosswalkRouting = EntityCrosswalkOutcome | ComplexRestructure
ComplexRestructure { sources: Set<M>, M ≥ 2, targets: Set<N>, N ≥ 2, humanReviewRequired: true }
```

Formally, `CrosswalkRouting = EntityCrosswalkOutcome | ComplexRestructure`, where
`ComplexRestructure { sources: Set<M>, M ≥ 2, targets: Set<N>, N ≥ 2, humanReviewRequired: true }`.
Crosswalk arity remains derived from the endpoint sets. `ComplexRestructure` is the fifth routing
family and cannot be treated as an ordinary entity-crosswalk outcome.

Suppression is not entity disappearance or an entity-crosswalk outcome. `AssertionDeltaKind` is
exactly `added-to-effective`, `removed-from-effective`, `replaced-in-effective`,
`qualified-in-effective`, `annotation-changed`, or `unchanged-context`. Every suppression requires a
nonempty `removed-from-effective` record containing the canonical source axiom; suppression leaves
the enhanced entity and its source crosswalk intact.

OntoPrism does not invent official entity-level versions. Official identity is:
**official release + official concept/role code + canonical source entity/assertion fingerprints and profile**.
Each immutable enhanced entity revision is a globally unique content-addressed revision under its
enhanced code. Enhanced release, overlay, and composition are membership contexts, not identity
components. Code equality, revision equality, and cache keys compare the enhanced code and content
revision; composition membership uses its separate context identity. An enhanced code is never reused
and is not replaced by NCI adoption.

Everything emitted is OntoPrism-governed enhanced NCIt content. Release-bound official NCIt
identifiers are source anchors and crosswalk endpoints, not necessarily the emitted enhanced primary
identifiers. Content remains derived from, aligned to, or corroborated by identified sources;
provenance does not transfer ownership. D86 qualifies D60 and does not supersede it.

## caDSR resolution and compatibility

caDSR source rows and anchors remain official NCIt codes; they are never rewritten. Enhanced
resolution derives through the release-bound crosswalk. `CadsrEnhancedResolution` reports a unique
target, split, merge, ambiguous, or unresolved result. A one-to-many crosswalk is `split` and returns
all targets unless a further qualified, approved mapping selects one target. `ambiguous` means
competing or incomplete records, not a correctly represented split. Ambiguous or unresolved outcomes
cannot look successful. Reports distinguish official-anchor coverage and enhanced-resolution coverage.

```text
CadsrEnhancedResolution =
  unique { target, crosswalk }
  | split { allTargets, crosswalk }
  | merged { target, sources, crosswalk }
  | ambiguous { candidates, reason }
  | unresolved { reason }
```

Its variants are exactly `unique { target, crosswalk }`, `split { allTargets, crosswalk }`,
`merged { target, sources, crosswalk }`, `ambiguous { candidates, reason }`, and
`unresolved { reason }`.

The four result families are not interchangeable: `EntityCrosswalkOutcome` describes entity endpoint
structure, `AssertionDeltaKind` describes an exact axiom operation, `CadsrEnhancedResolution` resolves
a caDSR official anchor through the crosswalk, and `MigrationReferenceOutcome` combines that result
with consumer context and may refuse. A caDSR one-to-many result remains a split with all targets
unless a further qualified, approved mapping makes it unique; multiple competing or incomplete
records are ambiguous rather than split.

Compatibility uses the discriminated result shape above against a named consumer profile and
dimension. Edited enhanced content must not serialize edited semantics under an official NCIt IRI.
The source export profile remains distinct from enhanced exports.

```text
CompatibilityResult =
  preserved { testedDenominator: PositiveInt, evidence: NonEmptySet<Evidence> }
  | changed { testedDenominator: PositiveInt, evidence: NonEmptySet<Evidence>, changes: NonEmptySet<Change> }
  | breaking { testedDenominator: PositiveInt, evidence: NonEmptySet<Evidence>, knownBreaks: NonEmptySet<Break> }
  | unknown { blockerOrReason: NonEmptyText }
```

The `unknown` variant has no success-denominator field. The other variants cannot represent a zero
denominator, and an overall result with any required `unknown` member cannot be compatible.

## Affected graph and dependent impacts

`AffectedGraphDiff` is a discriminated union. Both variants require a closure descriptor containing
profile, relation, direction, bounds, and boundary witnesses. Stated changes and finite-profile
inferred changes remain separate. The diff has completeness `complete-for-profile` or `incomplete`.
An incomplete diff cannot publish an incremental result and cannot carry certified complete change
sets or publication permission. Its required shape is:

```text
AffectedGraphDiff =
  CompleteForProfile { closure, statedChanges, finiteProfileInferredChanges }
  | Incomplete { closure, blockers, missingBoundaries }
```

The variants are `CompleteForProfile { closure, statedChanges, finiteProfileInferredChanges }` and
`Incomplete { closure, blockers, missingBoundaries }`.

The inferred comparison is the exact finite entailment, query, and signature set selected by a
versioned profile. Current runtime reasoning is disabled; target correction certification runs an
offline identified reasoner and profile over the exact effective composition.

Anything outside the declared graph boundary is not silently enumerated as a graph member. Non-graph
dependents are dependency-registry impacts, not graph members, and receive explicit
non-success/currentness treatment owned by #262. Unknown dependency coverage triggers #262's full-run
fallback or refusal. This design does not duplicate #262's impact vocabulary.

## Migration, reconciliation, and adoption

`MigrationReferenceOutcome` is derived from `CrosswalkRouting` plus consumer context; it combines
the crosswalk with consumer context and may refuse:

```text
MigrationReferenceOutcome =
  retained | redirected | expanded { allTargets, reviewRequired: true }
  | suppressed { replacement?, reason, reviewRequired: true }
  | ambiguous { candidates, reason } | unsupported { reason } | unresolved { reason }
```

Its variants are exactly `retained | redirected | expanded { allTargets, reviewRequired: true }`,
`suppressed { replacement?, reason, reviewRequired: true }`, and
`ambiguous { candidates, reason } | unsupported { reason } | unresolved { reason }`.

Expanded and suppressed references require review. Ambiguous, unsupported, and unresolved variants
cannot carry success fields or masquerade as success. On every later official
release, every correction is reconciled against exact source and enhanced identities. Nothing is
silently replayed, dropped, or overridden.

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
