# ONTOPRISM

<p align="center">
  <img src="https://img.shields.io/badge/python-3.13-3776AB?logo=python&logoColor=fff" alt="Python 3.13">
  <img src="https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=fff" alt="FastAPI">
  <img src="https://img.shields.io/badge/Svelte-5-FF3E00?logo=svelte&logoColor=fff" alt="Svelte 5">
  <img src="https://img.shields.io/badge/PostgreSQL-15-4169E1?logo=postgresql&logoColor=fff" alt="PostgreSQL 15">
  <img src="https://img.shields.io/badge/Tailwind-4-06B6D4?logo=tailwindcss&logoColor=fff" alt="Tailwind 4">
  <img src="https://img.shields.io/badge/license-Apache%202.0-blue" alt="Apache 2.0">
  <br>
  <img src="https://img.shields.io/badge/CI-passing-brightgreen" alt="CI passing">
  <img src="https://img.shields.io/badge/coverage-%E2%89%A590%25-brightgreen" alt="Coverage ≥90%">
</p>

**Pre-coordination Refactoring Into Semantic Modules.** An ontology exploration and
decomposition platform for the cancer-research domain over NCIt and caDSR.

ONTOPRISM refracts NCIt's **pre-coordinated** concepts into their **atomic** constituents so
complex meaning can be *composed* — expressed purely as combinations of simple concepts —
rather than baked into the terminology as thousands of named combinations.

## Background

### NCIt — NCI Thesaurus

The National Cancer Institute Thesaurus
([NCIt](https://ncithesaurus.nci.nih.gov/)) is a reference ontology covering the
cancer-research domain: diseases, drugs, genes, anatomy, procedures, and biological
processes. It contains ~200K concepts organised in a multi-rooted hierarchy and uses
OWL (Web Ontology Language) as its representation language.

NCIt models meaning through two kinds of relationships:
- **Hierarchical** (`rdfs:subClassOf`) — a disease is a kind of neoplasm
- **Role-based** (OWL existential restrictions) — a disease *has_finding_site* some organ

Pre-coordination is encoded in the stated OWL as **defined classes** —
`owl:equivalentClass` / `owl:intersectionOf` chains where each level intersects a
genus (a named superclass) with one or more role restrictions. The decomposition
engine reads the stated graph directly to recover the intended semantic parts.

### caDSR — Cancer Data Standards Repository

The [caDSR](https://cadsr.cancer.gov/) is the metadata registry for clinical research
data elements used across NCI programs. A **Common Data Element (CDE)** pairs a
*question* (e.g., "What is the histologic grade?") with a *value domain* (e.g.,
{Grade 1, Grade 2, Grade 3}) and is semantically anchored to NCIt concepts. caDSR
is the downstream consumer that makes decomposition useful: when NCIt concepts are
cleanly separated, CDE cross-links become precise and machine-actionable.

### Pre-coordination vs Post-coordination

**Pre-coordination** is the practice of creating a distinct named concept for every
specific combination of meaning. For example, instead of representing "Non-Small Cell
Lung Carcinoma" as a combination of "Lung Carcinoma" + "has_histology" → "Non-Small
Cell Carcinoma", NCIt defines it as a separate class under *Carcinoma* with the
histology detail baked into its definition and position in the hierarchy.

This is how NCIt has been maintained for decades — it works, but it leads to thousands
of highly specific concepts whose meaning is implicit in their name and definition
rather than formally decomposed into parts.

**Post-coordination** expresses complex meaning by combining simpler, atomic concepts
at query or use time. Instead of a named concept for "Non-Small Cell Lung Carcinoma",
a post-coordinated representation would say:

> *Lung Carcinoma* that *has_finding_site* → *Lung* and *has_associated_morphology* → *Non-Small Cell Carcinoma*

This is more flexible (any combination is expressible without awaiting a terminology
release) but requires a grammar — a set of roles that define how atomic concepts can
be combined.

## The Problem

NCIt contains tens of thousands of **pre-coordinated** concepts — named classes that
package multiple semantic dimensions into a single node (55,044 concepts carry two or
more role restrictions). For example, "Stage III Thyroid Gland Medullary Carcinoma
AJCC v7" and its near-duplicate "Stage III Thyroid Gland Medullary Carcinoma AJCC v8"
encode disease site, histology, abnormal cell, and staging version in one concept
each — identical clinical entities re-enumerated for a terminology update. This
approach:

- **Bloats the terminology** — every new combination requires a new concept
- **Hides meaning** — semantics are embedded in names and definitions rather than
  formal axioms, making them opaque to automated reasoning
- **Slows maintenance** — updating a dimension (e.g., staging terminology) means
  touching every pre-coordinated concept that encodes it
- **Limits flexibility** — researchers cannot query by arbitrary combinations of
  dimensions; they are limited to whatever combinations NCIt chose to name

## The Goal

Refactor NCIt's pre-coordinated concepts into their **atomic constituents** so that
complex meaning can be *composed* on demand — expressed purely as combinations of
simple concepts using formally defined roles — while keeping the original
pre-coordinated concepts intact for backward compatibility.

## The Vision — the long arc

The goal above is stage one of five. The end state is an oncology terminology that is
**systematically composed, grounded in vetted upstream ontologies, and demonstrably
covers what oncology actually talks about**. Each stage depends on the one before it,
and each has a guardrail that keeps it honest — stated here because the naive version of
each stage is subtly wrong, and the wrong version is the one that sounds better.

**1 · Decompose.** Every pre-coordinated NCIt concept gets a defining expression over
atomic constituents, with an `owl:equivalentClass` axiom that makes the decomposition
*reversible* (D19/D21).

> **Guardrail — the target is zero *unanalyzed* pre-coordination, not zero
> pre-coordinated concepts.** These are not the same thing, and only the first is
> coherent: an equivalence axiom needs a left-hand side, and caDSR's CDEs reference
> pre-coordinated NCIt codes, so deleting them would break the very anchoring the caDSR
> coverage guarantee exists to protect. GALEN attempted full elimination and was not
> adopted; SNOMED CT retains pre-coordination and *sanctions* post-coordination. We
> follow SNOMED. Success is: **no pre-coordinated concept without a sanctioned,
> reversible, genuinely atomic definition** — measured by `roundtrip_fidelity` (did we
> capture everything the source asserts?) and `residual_precoordination` (is what we
> produced actually atomic?).

**2 · Disambiguate the roles.** Some NCIt roles carry more than one sense (`R101` site
vs. region, `R105` cell-of-origin vs. lineage). Composition over a conflated role
produces confident nonsense, so the roles are split into univocal `op:` axes *before*
coverage is chased (D15/D17/D22: relation quality gates coverage).

**3 · Ground in the upstream substrate.** NCIt becomes the oncology-specific
**specialization layer** over vetted ontologies — Uberon and Cell Ontology for anatomy
and cell type, Mondo for disease genus — extended where oncology needs granularity the
substrate lacks.

> **Guardrail — a mapping layer, not a subset.** NCIt is *not* a subset of the upstream
> ontologies: it holds concepts with no upstream counterpart, and its class structure
> genuinely differs. The bridge is therefore **dual-canonical and additive** (D24–D26) —
> NCIt and caDSR anchoring are both preserved. And the substrate splits on licence:
> **Uberon/CL/Mondo are open and can be depended on definitionally; SNOMED CT and
> ICD-O-3 are licence-gated and may only be *mapped to***. An NCIt that is definitionally
> dependent on SNOMED cannot be redistributed, which would defeat the point.

**4 · Compare against the literature.** Embed and cluster PubMed oncology abstracts, and
compare that landscape with NCIt's.

> **Guardrail — this finds gaps; it does not measure balance.** Clustering abstracts
> yields a **literature-attention** landscape, and cosine distance in an embedding space
> is not semantic distance in an ontology. Publication counts are skewed by funding and
> fashion, so "NCIt disagrees with the embedding geometry" is not evidence of an NCIt
> defect. The falsifiable questions are: **which concepts does the literature discuss
> that NCIt cannot express, and which NCIt concepts does nobody ever use?**

**5 · Balance.** Drive granularity toward homogeneity — comparable semantic distance
between siblings (horizontally) and between parent and child (vertically) — across all
of oncology.

> **Guardrail — balance is a metric to improve, not an invariant to enforce.** Concept
> density in a real terminology follows clinical and research need; it is *supposed* to
> be uneven. Enforcing homogeneity would mean merging genuinely distinct concepts or
> minting concepts nobody needs — destroying information in the name of symmetry. So:
> **measure and publish the imbalance, and use it to target enrichment where coverage is
> demonstrably thin** relative to what stage 4 shows the field actually discusses.

Throughout, one non-negotiable: **every claim is measured, and a number that cannot move
is reported as such.** The published caDSR coverage figure (`COV`) exists precisely
because "interoperability for free" is otherwise unfalsifiable.

## The Approach

1. **Detect** — Identify pre-coordinated concepts via a semantic-type gate
   (neoplasm/disease/regimen branches; gene/protein role families excluded) and a
   defining-role count ≥ 2, excluding pure qualifier or value-set nodes. Each
   role-filler pair is a semantic dimension that can be factored out.

2. **Extract** — Walk each concept's genus chain recursively (a multi-parent DAG,
   not a single lineage). For each defining role, select the most-specific filler
   across alternate branches. Where role restrictions are absent (laterality,
   staging-manual version, "with/without \<finding\>"), fall back to NLP rule-based
   parsing of the concept label. The decisive feasibility finding: every role-defined
   constituent is already an existing, active NCIt concept — **100% coverage for
   the roles path** — so decomposition is surfacing and re-linking, not inventing.

3. **Flag** — Mark source concepts with `representationStatus="legacy-precoordinated"`
   so tooling and users can distinguish atomic from composite.

4. **Compose** — Enable post-coordinated queries through a query layer that
   combines atomic concepts at query time, supporting arbitrary dimension combinations
   without requiring named concepts.

The decomposition is **additive and reversible** — decomposed triples are written to
a separate named graph (`ncit_decomposed`), never mutating the stated OWL. Legacy
concepts remain fully navigable and resolvable; the decomposed view exists alongside
them as an alternative, more granular lens.

## Quickstart

```bash
pdm install                      # Python 3.13 deps
npm ci --prefix frontend         # SvelteKit deps
pdm run up                       # data services (Oxigraph + Postgres)
pdm run start-all                # backend :8011 + frontend :5175
```

Open [localhost:5175](http://localhost:5175). See [docs/DATA_SETUP.md](docs/DATA_SETUP.md)
for first-run provisioning.

## Project structure

```
ontolib/      Shared library — storage, NCIt/Uberon terminologies,
│             caDSR repository, decomposition engine
backend/      FastAPI app — repo/graph/search/sparql/refresh + decomposition API
frontend/     SvelteKit 5 app — Sigma + graphology graph explorer, dark/light UI
docs/         Architecture, design docs, decisions, data setup
scripts/      Dev tooling, data build, decomposition CLI, research helpers
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full layout and data-flow diagram.

## Status

| Layer | Status | Docs |
|---|---|---|
| NCIt + caDSR explorer | **Working** — search, browse, concept detail, graph explorer, CDE cross-links | |
| Decomposition engine | **Working** — detector, extractor, writer, CLI (`pdm run decompose`); SME golden-set curation loop landed (#44) | [design docs](docs/design/) |
| Extractor curation | **Ongoing** — expanding the SME-validated golden set | [#57](https://github.com/hniedner/ontoprism/issues/57) |
| External integration (dual-canonical) | **Phase-A foundation landed** — xref store, caDSR anchors, Uberon/CL candidates, ELK/ROBOT validation (#76 golden mapping set still open); Phase B–E pending | [#70](https://github.com/hniedner/ontoprism/issues/70) |
| Graph balancing | **Not started** — depends on trustworthy decomposition output | [#5](https://github.com/hniedner/ontoprism/issues/5) |
| Post-coordination grammar | **Not started** — depends on graph balancing | [#6](https://github.com/hniedner/ontoprism/issues/6) |

## Stack

| Category | Technologies |
|---|---|
| Backend | Python 3.13 · PDM · FastAPI · Oxigraph (SPARQL) · PostgreSQL + pgvector |
| Frontend | SvelteKit 5 · Tailwind 4 · Sigma + graphology · TypeScript |
| Quality | ruff · basedpyright · pytest · vitest · pre-commit · >90% coverage |

## Development

### Services

| Command | Action |
|---|---|
| `pdm run up` / `down` | Start/stop data containers (Oxigraph, Postgres) |
| `pdm run start-all` / `stop-all` / `restart-all` | Backend + frontend process supervision |
| `pdm run start-backend` / `stop-backend` / `restart-backend` | FastAPI on :8011 |
| `pdm run start-frontend` / `stop-frontend` / `restart-frontend` | SvelteKit on :5175 |
| `pdm run migrate` | Alembic schema migration |

Background logs go to `.dev-logs/`. Ports are offset from the sibling `fairdata` app — see
[docs/DATA_SETUP.md](docs/DATA_SETUP.md).

### Testing

```bash
pdm run test               # Hermetic: ontolib unit + backend unit/api/security + frontend vitest
pdm run test-unit          # Unit-marked only
pdm run test-integration   # Against live Oxigraph and Postgres
pdm run test-ci            # CI gate with ≥90% coverage
pdm run pre-commit run --all-files  # Local quality gate
```

### Architecture decisions

Key architectural decisions are documented in [docs/DECISIONS.md](docs/DECISIONS.md) (D1–D31)
and the [decomposition design series](docs/design/).

<!-- CODEBASE_LINE_COUNT_TABLE:START -->
## Codebase Line Count

_This table is auto-updated by CI after successful builds on `main`._

| Language | Files | Lines |
| --- | ---: | ---: |
| Python | 199 | 33,028 |
| JSON | 7 | 12,483 |
| Markdown | 19 | 5,259 |
| TypeScript | 62 | 4,280 |
| Svelte | 39 | 3,263 |
| CSS | 3 | 1,993 |
| YAML | 10 | 1,009 |
| TOML | 3 | 332 |
| Shell | 1 | 95 |
| JavaScript | 1 | 38 |
| HTML | 1 | 21 |
| **Total** | **345** | **61,801** |
<!-- CODEBASE_LINE_COUNT_TABLE:END -->

## Provenance

ONTOPRISM lifts the ontology vertical slice from the sibling `fairdata` codebase
(whole-package port of `ontolib`, `backend`, and `frontend`), deliberately leaving behind
fairdata's pipeline/HRM/learning/audit subsystems. See [docs/DECISIONS.md](docs/DECISIONS.md).
