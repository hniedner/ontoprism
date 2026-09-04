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

## Permanent release-forward reconciliation

[#316](https://github.com/hniedner/ontoprism/issues/316) owns the permanent target. A transfer binds
the exact old and new official NCIt release identities and exact overlay identities, then records a
per-assertion outcome. Automation may detect source changes, rebase unchanged assertions, produce
proposals, and run validation.

The system **refuses automatic replay and publication** for any unresolved, unsupported, or
ambiguous conflict: nothing is silently dropped or carried forward. Human review remains required
for semantic resolution, and reconciliation never assigns NCI adoption. This document defines the
boundary; it does not implement #316.

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
