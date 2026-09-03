# ONTOPRISM

<p align="center">
  <img src="https://img.shields.io/badge/python-3.13%E2%80%933.14-3776AB?logo=python&logoColor=fff" alt="Python 3.13–3.14">
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

## Terminology

This documentation uses plain language first and gives the ontology term in parentheses on
first use. Specialist design sections then use the precise term directly.

- **Decomposition** means exposing the distinct meanings packaged inside one complex concept
  while retaining that original concept. The result is an additive constituent view, not a
  deletion or a claim that the parts are already a logically equivalent replacement.
- A **dimension of meaning (axis)** says what kind of detail a constituent supplies, such as
  primary site, morphology, stage, or laterality.
- An **axis value (filler)** is the concept that supplies the detail on an axis, such as Lung
  (`C12468`) on the primary-site axis. In OWL, *filler* is the precise term for the class at
  the value end of a restriction.
- An **OWL statement that requires at least one relationship to a class (OWL existential
  restriction)** has the form “has primary site some Lung.” It states that instances of the
  disease have at least one such relationship; it is not a direct disease-to-organ data row.
- A **named class being further specialized (genus)** is the broader class in a logical
  definition. This is the description-logic sense of *genus*, not a biological taxonomic rank.
- A **broad NCIt category (semantic type)** classifies what kind of entity a concept is, such
  as `Neoplastic Process` or `Anatomical Structure`. It is coarser than the class hierarchy
  and is used to decide which algorithm applies, not to determine every relationship.
- A **reviewed, purpose-specific view (curated projection)** selects and organizes source facts
  for people and applications. It can intentionally omit detail, so ONTOPRISM keeps the complete
  source-derived record separately.
- A **particular assertion in the source definition (source occurrence)** records where a genus,
  role, and filler appeared in NCIt's stated OWL. Two identical-looking values can be different
  source occurrences when they came from different definition branches or groups.
- A **part-whole hierarchy (partonomy)** organizes structures by `part_of`, unlike the “is a kind
  of” class hierarchy. Part-whole evidence must not be treated as ordinary subclass evidence.
- A **set of relationships that belong together (relationship group)** preserves context among
  co-asserted details, following the grouping pattern used by SNOMED CT. Values in different
  groups must not be combined as though they described one clinical statement.

## Background

### NCIt — NCI Thesaurus

The National Cancer Institute Thesaurus
([NCIt](https://ncithesaurus.nci.nih.gov/)) is a reference ontology covering the
cancer-research domain: diseases, drugs, genes, anatomy, procedures, and biological
processes. It contains ~200K concepts organised in a multi-rooted hierarchy and uses
OWL (Web Ontology Language) as its representation language.

NCIt models meaning through two kinds of relationships:
- **Hierarchical** (`rdfs:subClassOf`) — a disease is a kind of neoplasm
- **Role-based** — a disease *has_finding_site* some organ, represented as an OWL
  existential restriction

Pre-coordination is encoded in the stated OWL as **defined classes** —
`owl:equivalentClass` / `owl:intersectionOf` chains where each level intersects a
genus with one or more role restrictions. The decomposition
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
AJCC v7" and "Stage III Thyroid Gland Medullary Carcinoma AJCC v8" each fuse disease
site, histology, abnormal cell, **and the staging edition** into one node.

**Note what this example is — and what it is not.** These two are *not* duplicates, and
the goal is not to merge them. The AJCC 8th edition is not a re-print of the 7th: where
v7 staged on anatomy alone (tumour size and spread), v8 folds in tumour biology — HPV
status in oropharyngeal cancer, depth of invasion in oral cancer — producing documented
**stage migration**, where the same patient is upstaged or downstaged between editions.
So "Stage III … v7" and "Stage III … v8" are *different clinical assertions about
different populations*, and collapsing them would destroy exactly the information the
edition exists to carry (D39).

The pre-coordination problem here is not redundancy but **fusion**: the staging edition
is a semantic dimension welded into the concept's name, so it cannot be reasoned over,
queried, or versioned independently. Decomposition factors it out into a first-class
staging axis (D23) — the two concepts then share a disease core and differ *explicitly*
in the axis that genuinely distinguishes them, instead of differing in a string. This
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
and four of the five carry a **guardrail** (stage 2's constraint is stated inline). Each
guardrail names **the version of that stage that sounds better** — the one you would put
on a slide — and then says why it fails. They are spelled out because in every case the
seductive form is the wrong one, and three of them would quietly *destroy information* if
built as stated.

**1 · Decompose.** Every pre-coordinated NCIt concept gets an additive constituent view.
The human-facing view is a deliberately lossy curated projection, but its representation
of record now preserves the complete stated multi-parent, grouped definition and traces
role- and parent-derived projected constituents back to source facts; minted NLP fallback
constituents retain separate proposal provenance (D50). It still does not assert
`owl:equivalentClass`, and `roundtrip_fidelity` remains unavailable until a separate
proof/validation step establishes exact reversibility (D19/D21/D43/D50).

> **Guardrail.** *Sounds better:* **"zero pre-coordinated concepts."** *Why it fails:*
> the target is zero **unanalyzed** pre-coordination, not zero pre-coordinated concepts. These are not the same thing, and only the first is
> coherent: an equivalence axiom needs a left-hand side, and caDSR's CDEs reference
> pre-coordinated NCIt codes, so deleting them would break the very anchoring the caDSR
> coverage guarantee exists to protect. GALEN attempted full elimination and was not
> adopted; SNOMED CT retains pre-coordination and *sanctions* post-coordination. We
> follow SNOMED. Success is: **no pre-coordinated concept without a sanctioned,
> reversible, genuinely atomic definition** — measured by `roundtrip_fidelity` (did we
> capture everything the source asserts?) and `residual_precoordination` (is what we
> produced actually atomic?). The second is **detector-relative**: it measures reducibility
> *as our detector sees it*, not ground-truth atomicity, so a better detector moves the
> number with no ontology change. It is therefore pinned against the SME-curated golden set,
> where drift becomes visible (D37).

**2 · Disambiguate the roles.** Some NCIt roles carry more than one sense (`R101` site
vs. region, `R105` cell-of-origin vs. lineage). Composition over a conflated role
produces confident nonsense, so the roles are split into univocal `op:` axes *before*
coverage is chased (D15/D17/D22: relation quality gates coverage).

**3 · Ground in the upstream substrate.** NCIt becomes the oncology-specific
**specialization layer** over vetted ontologies — Uberon and Cell Ontology for anatomy
and cell type, Mondo for disease genus — extended where oncology needs granularity the
substrate lacks.

> **Guardrail.** *Sounds better:* **"NCIt becomes a subset of the vetted
> ontologies."** *Why it fails:* NCIt is *not* a subset of the upstream ontologies: it holds concepts with no upstream counterpart, and its class structure
> genuinely differs. The bridge is therefore **dual-canonical and additive** (D24–D26) —
> NCIt and caDSR anchoring are both preserved. And the discriminator is **dependency,
> not licence** (D38.2b, D60): *align, do not depend; learn, do not copy; corroborate,
> do not inherit.* **No external source — open or licence-gated — may be a definitional
> dependency.** An NCIt that cannot resolve without Uberon is a dependent ontology
> whether or not Uberon is CC-BY, and one definitionally dependent on SNOMED cannot be
> redistributed at all. External codes are therefore carried as **alignment annotations,
> never as definitional fillers**. Licence still governs one narrower thing: *publicly
> serving* SCTIDs and ICD-O-3 codes to unlicensed consumers remains entitlement-gated
> (D29.3).
>
> **And "grounded in" is not "losslessly equivalent to."** The mapping layer is a standing
> maintenance liability, not a one-time conquest: cross-ontology maps rot at roughly
> **6–10% per upstream release** (hence the D29 lifecycle and the staleness sweep), and
> SKOS `broadMatch`/`narrowMatch` are **not** identity — only a validated `exactMatch` is.
> Today `COV` is still ~0. This is the stage with the most distance left to travel, and it
> is measured precisely so that nobody can claim otherwise.

**4 · Compare against the literature.** Embed and cluster PubMed oncology abstracts, and
compare that landscape with NCIt's.

> **Guardrail.** *Sounds better:* **"cluster the literature and see where NCIt's shape
> disagrees."** *Why it fails:* this finds **gaps**; it does not measure balance. Clustering abstracts
> yields a **literature-attention** landscape, and cosine distance in an embedding space
> is not semantic distance in an ontology. Publication counts are skewed by funding and
> fashion, so "NCIt disagrees with the embedding geometry" is not evidence of an NCIt
> defect. The falsifiable questions are: **which concepts does the literature discuss
> that NCIt cannot express, and which NCIt concepts does nobody ever use?**

**5 · Balance.** Drive granularity toward homogeneity — comparable semantic distance
between siblings (horizontally) and between parent and child (vertically) — across all
of oncology.

> **Guardrail.** *Sounds better:* **"make semantic distance uniform across oncology."**
> *Why it fails:* balance is a metric to **improve**, not an invariant to **enforce**. Concept
> density in a real terminology follows clinical and research need; it is *supposed* to
> be uneven. Enforcing homogeneity would mean merging genuinely distinct concepts or
> minting concepts nobody needs — destroying information in the name of symmetry. So:
> **measure and publish the imbalance, and use it to target enrichment where coverage is
> demonstrably thin.**
>
> **Stage 4's guardrail carries forward, or it was decoration.** Enrichment driven by
> *publication density* would enrich where the field publishes — importing funding and
> fashion into the terminology's shape one evidence-looking step at a time. So enrichment
> is targeted on the **falsifiable signal only**: concepts the literature can express that
> NCIt cannot. **A cluster being large is not a reason to subdivide a branch.**

Throughout, one non-negotiable: **every claim is measured, and a number that cannot move
is reported as such.** The published caDSR coverage figure (`COV`) exists precisely
because "interoperability for free" is otherwise unfalsifiable.

### Serving the downstream NCI ecosystem

A cross-cutting objective (not a sixth sequential stage): the decomposed, substrate-grounded
NCIt must be **usable by the programs that actually collect oncology data** — starting with
**cancer registries**. The touchpoint is **caDSR**, which already registers the NAACCR/SEER
data standards and anchors each data element to NCIt. The posture is *backbone, not
replacement*: NCIt becomes the reference terminology a FHIR/mCODE-modernized NAACCR binds to
through caDSR; NAACCR keeps its exchange format, operational rules, and mandate.

> **Guardrail — measured through caDSR, and never by importing NAACCR's flat legacy.** Registry
> mappability is the existing `COV` *scoped to the NAACCR/SEER caDSR-CDE subset*; its critical
> path is the value-meaning workstream (#75), and every mapping runs through the decomposed
> `op:` representation with an honest predicate. Full strategy, tactics, and references:
> [`docs/ecosystem/ncit-cadsr-naaccr.md`](docs/ecosystem/ncit-cadsr-naaccr.md) — first of a
> series covering the NCI CTRP / ClinicalTrials.gov, CRDC, and CCDI relationships.

## The Approach

1. **Detect** — Select the `neoplasm` or `disease` population from NCIt's stated
   named-class hierarchy (direct subclass plus defined-class genus edges), then apply
   the shared axis algorithm's semantic-type and defining-axis gates. The disease
   branch contains the neoplasm branch; regimen remains reserved for its distinct
   component-bag algorithm. Each supported, projectable role-filler pair can be factored
   out; filtered source facts remain available in the complete structural record.

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

The current decomposition is **additive and non-destructive**: decomposed triples are
written to a separate named graph (`ncit_decomposed`), never mutating the stated OWL.
Legacy concepts remain fully navigable and resolvable; the curated decomposed view exists
alongside them as an alternative, more granular lens. Exact reversibility is a future
proof-bearing contract (#153), not a property claimed by this projection.

## Quickstart

```bash
pdm install --dev                # Use Python 3.13 for the production/default environment
npm ci --prefix frontend         # SvelteKit deps
cp .env.example .env
pdm run python scripts/install_jena.py --install-dir "$PWD/.tools/jena-6.1.0"
pdm run python scripts/install_robot.py --install-dir "$PWD/.tools/robot-1.9.10"
pdm run data-build owl           # download and certify the NCIt OWL pair
pdm run data-build ncit-bootstrap # build the first NCIt QLever index
pdm run data-build uberon-store  # build the Uberon/CL QLever index
pdm run up                       # start QLever + Postgres
pdm run migrate
pdm run start-all                # backend :8011 + frontend :5175
pdm run python-314-compatibility # isolated 3.14 import + non-integration certification
```

Keep `python3.14` discoverable on `PATH`: mandatory `pdm run verify` includes that
compatibility lane. Python 3.14 certification covers hermetic imports and the
non-integration unit suite; integration, data-build execution, and the production
container remain on Python 3.13.

Open [localhost:5175](http://localhost:5175). See [docs/DATA_SETUP.md](docs/DATA_SETUP.md)
for first-run provisioning. Entitled ICD-O operators set the server-only
`ICDO_ENTITLEMENT_KEY` in `.env`; `ENABLE_LICENSED_MAPPINGS` remains a separate opt-in for
ICD-O-bearing NCIt responses.

## Project structure

```
ontolib/      Shared library — storage, NCIt/Uberon terminologies,
│             caDSR repository, decomposition engine
backend/      FastAPI app — repo/graph/search/refresh + decomposition API
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
| Extractor curation | **Pending final attestation** — the 20-concept source-bound/augmented review packet and reporting are ready, but are not yet the scorer oracle | [#57](https://github.com/hniedner/ontoprism/issues/57) · follow-ups [#261](https://github.com/hniedner/ontoprism/issues/261), [#262](https://github.com/hniedner/ontoprism/issues/262), [#263](https://github.com/hniedner/ontoprism/issues/263) |
| External integration (dual-canonical) | **Phase-A foundation landed** — xref store, caDSR anchors, Uberon/CL candidates, ELK/ROBOT validation (#76 golden mapping set still open); Phase B–E pending | [#70](https://github.com/hniedner/ontoprism/issues/70) |
| Graph balancing | **Not started** — depends on trustworthy decomposition output | [#5](https://github.com/hniedner/ontoprism/issues/5) |
| Post-coordination grammar | **Not started** — depends on graph balancing | [#6](https://github.com/hniedner/ontoprism/issues/6) |

## Stack

| Category | Technologies |
|---|---|
| Backend | Python 3.13–3.14 (3.13 production/default) · PDM · FastAPI · QLever (SPARQL) · PostgreSQL + pgvector |
| Frontend | SvelteKit 5 · Tailwind 4 · Sigma + graphology · TypeScript |
| Quality | ruff · basedpyright · pytest · vitest · pre-commit · >90% coverage |

## Development

### Services

| Command | Action |
|---|---|
| `pdm run up` / `down` | Start/stop data containers (QLever, Postgres) |
| `pdm run start-all` / `stop-all` / `restart-all` | Backend + frontend process supervision |
| `pdm run start-backend` / `stop-backend` / `restart-backend` | FastAPI on :8011 |
| `pdm run start-frontend` / `stop-frontend` / `restart-frontend` | SvelteKit on :5175 |
| `pdm run python-314-compatibility` | Clean locked Python 3.14 runtime-import and non-integration lane; also included by `verify` |
| `pdm run migrate` | Alembic schema migration |

Background logs go to `.dev-logs/`. Ports are offset from the sibling `fairdata` app — see
[docs/DATA_SETUP.md](docs/DATA_SETUP.md).

### Testing

```bash
pdm run test               # Hermetic: ontolib unit + backend unit/api/security + frontend vitest
pdm run test-unit          # Unit-marked only
pdm run test-integration   # Safe default: nonce-owned disposable Postgres/QLever
pdm run test-integration-full-store  # Explicit read-only contracts against configured corpora
pdm run test-ci            # CI gate with ≥90% coverage
pdm run pre-commit run --all-files  # Local quality gate
```

When the certified stated NCIt corpus is served separately from the configured inferred
store, set `NCIT_STATED_SPARQL_URL` for the read-only full-store gate. For the M1 26.07d
review setup: `NCIT_STATED_SPARQL_URL=http://localhost:7890 pdm run test-integration-full-store`.

### Architecture decisions

Key architectural decisions are documented in [docs/DECISIONS.md](docs/DECISIONS.md) (D1-D60)
and the [decomposition design series](docs/design/).

<!-- CODEBASE_LINE_COUNT_TABLE:START -->
## Codebase Line Count

_This table is auto-updated by CI after successful builds on `main`._

| Language | Files | Lines |
| --- | ---: | ---: |
| Python | 429 | 170,532 |
| JSON | 31 | 59,995 |
| Markdown | 36 | 11,857 |
| TypeScript | 113 | 8,494 |
| Svelte | 68 | 4,285 |
| CSS | 3 | 1,993 |
| YAML | 10 | 1,250 |
| TOML | 5 | 1,047 |
| Shell | 1 | 105 |
| JavaScript | 1 | 38 |
| HTML | 1 | 21 |
| **Total** | **698** | **259,617** |
<!-- CODEBASE_LINE_COUNT_TABLE:END -->

## Provenance

ONTOPRISM lifts the ontology vertical slice from the sibling `fairdata` codebase
(whole-package port of `ontolib`, `backend`, and `frontend`), deliberately leaving behind
fairdata's pipeline/HRM/learning/audit subsystems. See [docs/DECISIONS.md](docs/DECISIONS.md).
