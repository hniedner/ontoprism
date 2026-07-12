# NCIt as a Specialization of the OBO/SNOMED Reference Substrate — External-Ontology Integration Strategy & Implementation Plan

**Status:** Design-of-record (strategy shift) · **Author:** Hannes Niedner · **Date:** 2026-07-11
**Supersedes nothing; extends** the [decomposition assessment](./ncit-decomposition-assessment.md),
[engine design](./ncit-decomposition-engine.md), the [post-coordination literature
review](../postcoordination-literature-review.md), and DECISIONS **D14–D23**.
**Decisions introduced here:** **D24–D26** (see [`../DECISIONS.md`](../DECISIONS.md)).

> **Origin.** This document responds to external feedback (a local input memo) recommending
> that a next-generation NCIt be built on the OBO Foundry stack (**Uberon** anatomy, **Cell
> Ontology** cells) plus **SNOMED CT / ICD-O-3** morphology and **Mondo / DO** disease, linked by
> Relation-Ontology properties. The feedback is directionally aligned with where ONTOPRISM already
> is (SNOMED relationship groups adopted in D19; RO univocal relations the governing principle in
> D22; SCG/ECL/MRCM grammar in D22; FHIR `ConceptMap.$translate` as the equivalence surface), but
> its literal prescription — *extract NCIt's anatomy/cell axes and replace them with upstream IRIs*
> — collides with the project's load-bearing invariant (additive, never mutate the stated OWL) and
> with the requirement to map back to NCIt concepts **and** caDSR CDEs. This plan takes the correct
> intent of the feedback and folds it in without breaking those invariants.

---

## 0. Implementation status (2026-07-12)

**Phase A is substantially implemented and TDD-tested** — the design below is now partly a record of
built code, not only a forward plan. Verified against the tree:

| Area | Status | Where |
|---|---|---|
| SSSOM record + SKOS/lifecycle vocab | **Done** | `ontolib/src/ontolib/repositories/xref/models.py`, `vocab.py` |
| Postgres `xref_run`/`concept_xref` + migration | **Done** | `migrations/versions/0004_xref.py`; `repositories/xref/store.py` (`XrefStore`) |
| Additive named graph `NCIT_UPSTREAM_XREF_GRAPH_IRI` | **Done** | `repositories/xref/vocab.py`, `ttl_writer.py` |
| caDSR anchor enumeration + scope gate + liveness (D27 §13.1–13.2) | **Done** | `repositories/xref/cadsr_anchors.py` (`enumerate_anchors`, `filter_in_scope`, `check_liveness`) |
| Uberon/CL candidate ingest (`closeMatch`) | **Done** | `repositories/xref/candidate_ingest.py` (`generate_candidates`, `ingest_candidates`) |
| Non-circular ELK/ROBOT validation (D28) | **Done (primitives)** | `repositories/xref/validation.py` (`validate_and_classify`, `promote_candidate`); `docs/DATA_SETUP.md` ROBOT+ELK+Java 21 |
| Tests (RED→GREEN) | **Done** | `ontolib/tests/repositories/xref/` (8 files) |
| **Golden mapping set + `exactMatch` precision scorer** (part of #76) | **GAP** | no curated golden mappings; only `closeMatch` candidates exist |
| **caDSR *CDE-level* coverage report (§13.3 `COV`)** (generator: #76; published: #83) | **GAP** | only *filler-level* `candidate_coverage_report` exists |
| **xref orchestration CLI + `data-build` stage + Uberon-client wiring** | **GAP** | `ingest_candidates` exists but no `scripts`/`data_build` entrypoint; `uberon_sparql_url` unused |
| **Validation end-to-end** (build merged EL ontology per candidate → classify → gather evidence → persist promotion + lifecycle) | **GAP** | `promote_candidate` is a pure fn taking `el_valid`; nothing drives it over real candidates |
| **Backend serve** `/concept/{id}/mappings` + `$translate` (#82) | **GAP** | no `xref` router in `backend/` |
| **Phase B**: bind `upstream_xref` to `op:` fillers / cross-product (#77); Mondo genus (#79); Uberon `part_of` spike (#78) | **Not started** | — |
| **Phase C**: SNOMED/ICD-O-3 (#80), morphology-from-parent (#81), value/qualifier mapping (#75) | **Not started** | — |

The **GAP/Not-started rows are the real "next issues"** (epic **#70**; roadmap §5). The immediate next
is **#76** (golden mapping set + CDE-level coverage-report generator), then Phase B (#77). Per-issue
TDD implementation plans live in the ephemeral `plan-issue-NN-*.md` working notes.

**Codebase corrections to this doc's earlier assumptions** (the design predates the code): the xref
module lives under `repositories/xref/` (not `terminologies/xref/`); there is **no ORM** — Postgres is
raw SQL via `sqlalchemy.text()` and hand-written Alembic migrations (`target_metadata=None`); there is
**no `terminologies/uberon/` module** — Uberon is queried through the generic `OxigraphHttpClient` and
its SPARQL lives in `candidate_ingest.py`; ports are **NCIt :7888, Uberon :7889, Postgres :5433** (not
7878/7879/5432); the NCIt role queries are in `terminologies/ncit/role_queries.py`. §8 paths below are
annotated accordingly.

---

## 1. Executive summary

The strategic shift is one sentence: **NCIt becomes the oncology-specific *specialization* of a
shared, vetted upstream substrate — Uberon (anatomy), Cell Ontology (normal cells), SNOMED CT +
ICD-O-3 (abnormal morphology/histology), and Mondo/DO (disease genus) — rather than a self-contained
silo that re-models all of those domains itself.** NCIt keeps what is genuinely its own — the
oncology-specific pre-coordinated combinations, AJCC staging, chemotherapy regimens, and the
NCI-curated oncology vocabulary — and *defers* the general anatomy/cell/disease/morphology
scaffolding to the upstream reference ontologies by **mapping to them**, not by absorbing or
replacing them.

This is realized as a **dual-canonical, additive bridge** (per the user's chosen integration
posture):

- **Reference plane — NCIt (unchanged, canonical-of-record for everything that exists today).**
  Every legacy `Cxxxxx` IRI stays resolvable, un-mutated, and remains the anchor that caDSR CDEs
  point at. This is the D4/D19/§6 additivity invariant, now load-bearing across ontologies too.
- **Canonical plane — the upstream OBO/SNOMED stack (canonical for *new* post-coordinated
  authoring and for cross-terminology interoperability).** New expressions are written against
  Uberon/CL/SNOMED/ICD-O-3/Mondo atoms.
- **Join — a mapping layer** (`skos:*Match` + RO relations + FHIR `ConceptMap.$translate`) that
  translates any atom between the two planes, in both directions, with stated confidence and
  provenance, DL-validated (D21-consistent, never `rdfs:subClassOf+`).

The mapping target is a **bounded, enumerable set of NCIt concepts** — but it is *not* simply the
~20,021 role-target atoms decomposition surfaces (assessment §3.2). Those atoms are the *fillers* of
decomposed concepts; the concepts **caDSR** anchors to are a *different, overlapping* set (the
pre-coordinated concept itself, its object-class/property concepts, and — critically — the
value-domain *permissible-value meaning codes* such as *Grade 1/2/3*, *Positive/Negative*,
laterality, which the assessment §3.4 confirms are **not** modelled as role fillers). The mapping
target is therefore the **union** of (a) the neoplasm-branch role-target atoms and (b) the distinct
NCIt concepts actually referenced by in-scope caDSR CDEs — enumerated from the caDSR read model, not
assumed. §13 makes this set explicit and measurable; it is the correction that turns "map to caDSR"
from an assertion into a verifiable guarantee.

A large volume of *candidate* cross-references already exists — NCI Metathesaurus CUIs (NCIt↔SNOMED↔
ICD-O-3), Mondo's NCIt xrefs, Uberon/CL/FMA xrefs — which materially lowers cost. But **existence of
an xref is not equivalence.** UMLS CUI co-occurrence encodes editorial/lexical synonymy, not logical
class equivalence; peer-reviewed audits of the oncology case specifically find NCIt↔ICD-O-3/ICD-10
maps *missing and inconsistent* (Abdelhak et al., *J Biomed Semantics* 2017, PMC5294908) and general
inter-terminology mapping precision often low. So the honest economics are: **candidate ingest is
cheap; upgrading candidates to inference-grade `owl:equivalentClass` is curation-grade authoring**
(§4, §14). The work is *ingest → normalize → curate/validate → serve*, and the curate step is the
cost centre, not a formality.

The oncology concept itself is then expressible as an OBO-style **cross-product / logical
definition** (lit review §4.2, GO cross-products [26]): an NCIt neoplasm's decomposed `op:` axes
point at upstream fillers, so the concept reads as *«a Mondo disease genus» that `op:PrimarySite`
some «Uberon site» and `op:CellType` some «SNOMED/ICD-O-3 morphology»* — NCIt supplying the
oncology-specific specialization, the substrate supplying the reusable parts. That is precisely what
"NCIt as an extension/specialization of these ontologies" means, made concrete.

**What this plan is not.** It is not a re-platforming, not a migration off NCIt, and not a mutation
of the stated OWL. caDSR is never touched: its CDEs keep resolving exactly as today (an additivity
property of the *substrate*). Their **upstream reach is not "for free"** — it exists only for those
caDSR-anchored NCIt concepts that are actually in the mapped set, with an identity-grade link. §13
replaces the earlier "transitive, by construction" framing with an *enumerate-then-measure* workstream
whose coverage number is a first-class deliverable. Additivity guarantees nothing *stored* breaks; it
does **not** guarantee the *new* served surfaces (`$translate`, `/mappings`) are correct — those need
their own correctness gate (§12).

---

## 2. What we keep — inventory of aligned prior work that carries over unchanged

The single most important framing for execution: **this is mostly a re-use exercise, not a rewrite.**
Every row below is existing, decided, or built work that the new architecture consumes as-is.

| Prior work | Decision / doc | Role in the new architecture |
|---|---|---|
| Roles-first decomposition; 100% filler coverage | assessment §3.2; README | The ~20K role-target atoms are **one** of the two mapping-target sets (§13); the caDSR anchor set `C_cadsr` is the other and is *not* a subset. Bounded, enumerable, already extracted. |
| `op:` univocal axes (`op:PrimarySite`, `op:CellType`, `op:MetastaticSite`, `op:StageSystem`, `op:MolecularAbnormality`, …) | D17, D20, D22, D23 | These **are** the Relation-Ontology `has_location`/`derives_from`-style univocal relations the feedback asks for. We already have them. We now give each a stated domain/range that references **upstream** classes. |
| SNOMED-style relationship groups | D19 | Already the target representation for co-equal axes. The SNOMED morphology axis and multi-valued site axes slot straight in. |
| Complete lossless `owl:equivalentClass` unfolding behind `--emit-equivalence` | D19, D21 | Becomes the **cross-product** artifact: the same unfolding, with fillers additionally carrying upstream equivalents. |
| Single-most-specific view as an explicitly-lossy curated projection | D15, D19 | Unchanged. Projection now optionally renders upstream labels. |
| SCG / ECL / MRCM grammar template for goal 4 (#6) | D22 | The post-coordination grammar now **sanctions over upstream ranges** (MRCM ranges reference Uberon/SNOMED). Interoperable by construction. |
| FHIR `ConceptMap.$translate` as the pre-↔post equivalence surface | D22, lit §8.4 | Becomes the **dual-canonical join surface** between the NCIt plane and the upstream plane. |
| Uberon store already running at `:7879` | assessment §8, D16, DATA_SETUP | Infra already present. Re-purposed from "validation cross-check" to "xref + validation target" (D16 revisit — §11). |
| Stated-OWL additivity; named-graph separation (`ncit_decomposed`) | D4, D12, D19 | The xref triples live in **new named graphs alongside** `ncit_decomposed`. Same discipline. |
| DL-classification / real reasoner as the equivalence oracle; never `rdfs:subClassOf+` | D21, lit §5.2, §8.3 | Now gates **cross-ontology** mapping validation too — the highest-risk correctness item. |
| Scope gate: disease/neoplasm(/regimen) only; gene/protein excluded | assessment §1, §3.3 | Held. Upstream integration inherits the same scope gate. |
| caDSR read model (`repositories/cadsr`) anchored on NCIt concepts | ARCHITECTURE, README | Untouched; gains transitive upstream reach (§7). |

The net-new engineering (§8) is a mapping ingest/store/serve layer and a grammar-range binding — not
a new decomposition engine.

---

## 3. Target architecture — four planes joined by one mapping layer

```
                          ┌──────────────────────────────────────────────┐
   UPSTREAM (canonical     │  Uberon        Cell Ontology     Mondo / DO   │
   for new authoring &     │  (anatomy)     (normal cells)    (disease)    │
   interop)                │        SNOMED CT  +  ICD-O-3 (morphology)     │
                          └───────────────▲──────────────────────────────┘
                                          │  skos:exactMatch / closeMatch
                                          │  RO: has_location, derives_from,
                                          │      has_material_basis_in
                          ┌───────────────┴──────────────────────────────┐
   MAPPING LAYER          │  ncit_upstream_xref  (named graphs, additive)  │
   (the join;             │  provenance + confidence + SKOS relation       │
   DL-validated)          │  FHIR ConceptMap.$translate  (both directions) │
                          └───────────────▲──────────────────────────────┘
                                          │  op:PrimarySite / op:CellType /
                                          │  op:Morphology / op:StageSystem …
                          ┌───────────────┴──────────────────────────────┐
   DECOMPOSED PLANE       │  ncit_decomposed  (op: univocal axes, D17–D23) │
   (op: cross-products)   │  legacy-precoordinated ⟶ hasConstituent[axis]  │
                          └───────────────▲──────────────────────────────┘
                                          │  additive annotations only
                          ┌───────────────┴──────────────────────────────┐
   REFERENCE PLANE        │  NCIt stated OWL (Cxxxxx) — NEVER MUTATED       │
   (canonical-of-record,  │  ← caDSR CDEs anchor here, unchanged           │
   backward-compatible)   └────────────────────────────────────────────────┘
```

**Reading the stack bottom-up.** The NCIt stated OWL is the immutable base; the decomposition engine
writes `op:` axes into `ncit_decomposed`; the mapping layer attaches upstream equivalents to each
axis filler; new authoring and external consumers work in the upstream plane and are translated down
to NCIt (and thus to caDSR) on demand.

### 3.1 The cross-product model (how NCIt becomes a "specialization")

A decomposed NCIt neoplasm is asserted as a defined class over a Mondo/DO genus and upstream
differentia. Illustrative (C6135, *Stage III Thyroid Gland Medullary Carcinoma AJCC v7*):

```turtle
# Reference plane (unchanged): C6135 owl:equivalentClass [ ... NCIt intersection ... ]

# Decomposed plane (op: axes, D23 organ-level R101):
C6135  :representationStatus "legacy-precoordinated" ;
       op:PrimarySite     C12400 ;      # Thyroid Gland (NCIt)
       op:CellType        C36825 ;      # (D15 most-specific)
       op:StageSystem     C90530 ;      # AJCC v7
       op:StageValue      C27970 .      # Stage III

# Mapping plane (additive xref, new):
C12400 skos:exactMatch    UBERON:0002046 ;         # Thyroid gland
       skos:exactMatch    SCTID:69748006 .
C36825 skos:closeMatch    <ICDO3:8345/3-morphology> .

# Cross-product (behind --emit-equivalence, upstream-bound):
C6135  owl:equivalentClass [
         a owl:Class ;
         rdfs:subClassOf  <MONDO:medullary_thyroid_carcinoma> ;  # disease genus
         op:PrimarySite   some UBERON:0002046 ;                  # anatomy substrate
         op:CellType      some <morphology-substrate> ] .
```

NCIt's *unique* contribution here is the oncology-specific packaging plus staging (`op:StageSystem`
/ `op:StageValue`) and, elsewhere, regimens — none of which Uberon/CL/SNOMED model. The anatomy, cell,
disease-genus, and morphology fillers are *borrowed* from the substrate via the mapping plane. This
is the OBO cross-product pattern (lit §4.2 [26]) applied to make NCIt an application ontology over a
reference substrate.

### 3.2 Dual-canonical rule (which plane is authoritative when)

| Situation | Canonical plane | Why |
|---|---|---|
| Any concept/CDE that exists today | **NCIt** | Backward compatibility; caDSR anchoring; concept permanence [Cimino desiderata]. |
| New post-coordinated authoring | **Upstream** | Interoperability, orthogonality, no combinatorial minting. |
| Cross-terminology query / data exchange | **Upstream** | FHIR/OBO ecosystem speaks Uberon/SNOMED/Mondo. |
| Round-trip / reversibility proof | **NCIt** (via `owl:equivalentClass` unfolding) | The lossless record of truth is the NCIt definition (D19). |

The two planes are **always joined** — never a fork. Every upstream atom used in authoring has a
mapping back to an NCIt atom (or an explicit "no NCIt equivalent — minted" record, cf. D23 minted
concepts), so translation is total in both directions or explicitly flagged where it is not.

---

## 4. The mapping layer — sources, semantics, provenance, validation

### 4.1 Mapping sources (reuse existing maps before authoring new ones)

| Upstream target | Primary mapping source | Pivot | Notes |
|---|---|---|---|
| **SNOMED CT**, **ICD-O-3** | **NCI Metathesaurus (NCIm)** | UMLS **CUI** | NCIm already bridges NCIt↔SNOMED↔ICD-O-3 across millions of terms (assessment feedback §2.2). Highest-yield, lowest-effort source. |
| **Mondo / DO** | Mondo's own `xref`/`skos` to NCIt | direct | Mondo is built to synthesize NCIt+DO+SNOMED; NCIt xrefs ship in Mondo. Ingest and invert. |
| **Uberon** | Uberon `xref` (NCIt, FMA, SNOMED) + label/synonym match | direct + lexical | Uberon carries NCIt/FMA/SNOMED xrefs; gaps filled by curated lexical match against the NCIt anatomy branch, DL-validated. |
| **Cell Ontology (CL)** | CL `xref` + label match | direct + lexical | Same pattern as Uberon for the normal-cell axis (`op:CellOrigin`). |

**Principle (mirrors "atoms already exist"): map-before-mint.** A candidate mapping is only *authored*
when no vetted source supplies it; authored mappings enter the same review/provenance workflow as
D23's minted concepts.

### 4.2 Mapping semantics — SSSOM records, honest SKOS relations, and a hard separation of *annotation* from *logic*

Every cross-ontology link is stored as an **SSSOM record** (Matentzoglu et al., *Database* 2022) —
subject, predicate, object, **mapping justification**, confidence, author, and **source release
versions of both endpoints**. Bare xrefs without this metadata are not reusable and are the reason
legacy crosswalks cannot be trusted; SSSOM is now the community standard and is what Mondo itself
uses. The predicate is the *true* relation, never a flat `owl:sameAs`:

- `skos:exactMatch` — interchangeable senses. **Promoted only through the §4.4 gate; never asserted
  from a shared UMLS CUI alone** (a CUI is editorial synonymy, not class equivalence — H1).
- `skos:closeMatch` — near-equivalent, safe for retrieval, **not for inference**.
- `skos:broadMatch` / `skos:narrowMatch` — granularity mismatch (common: NCIt organ vs Uberon subsite).
  **Directional and non-composing:** a chain `exact → broad` yields a *broader* reach, not identity;
  even `skos:exactMatch` is not formally transitive in SKOS, so multi-hop reach must be reported
  per-predicate, not collapsed to "reaches upstream."
- `skos:relatedMatch` — associative only.

**Critical distinction (annotation vs logic).** SKOS mapping properties are *annotation properties
with no logical semantics* — deliberately not `owl:equivalentClass`. They **must not** be fed to a
reasoner as equivalences (doing so imports every mapping error as an ontological axiom and risks
global unsatisfiability — C2). The identity used for logical inference (cross-product genus/filler
substitution, §3.1) is a **separate, curated `owl:equivalentClass` bridge axiom** that exists only
where independently justified, and is *not* the SKOS annotation. A mapping may never be its own
validation evidence.

RO object properties carry *typed non-identity* cross-ontology links where the relation is **not**
equivalence — and this is the correct predicate for the cell↔morphology axis, which pairs three
different ontological categories (an NCIt *cell*, an ICD-O-3 *morphology/behaviour* code, a SNOMED
*morphologic-abnormality* entity): `RO:derives_from` (abnormal cell → CL normal cell / morphology),
`located_in` (neoplasm → Uberon site), `has_material_basis_in` (Mondo disease → Uberon site). These
bridge; they never equate a cell with a morphology (M1).

### 4.3 Provenance & confidence (governance parity with decomposition)

Every mapping triple carries, in a sidecar Postgres table (`concept_xref`, mirroring `decomp_*`):
source (`ncim` | `mondo_xref` | `uberon_xref` | `cl_xref` | `lexical` | `manual`), SKOS relation,
confidence, UMLS CUI where applicable, curator review status (`proposed`/`approved`/`rejected`, reusing
D23's governance model), and run/version provenance. No mapping is trusted silently; the ≥0.9 gate
concept (D21/#44) extends to mapping precision against a golden mapping set.

### 4.4 Validation — non-circular, EL-profiled, with committed reasoner infrastructure (D21 extended, D28)

Promoting a candidate to an inference-grade `owl:equivalentClass` bridge is the single highest-risk
correctness item. The gate has four hard requirements (D28):

1. **Non-circularity.** The evidence for `equivalentClass(NCIt_x, Upstream_y)` may **not** be the
   mapping itself. Evidence is independent: label/definition agreement plus corroborating structural
   signals (shared parents/children after a *separate* trusted anchor mapping, xref agreement across
   ≥2 independent sources, or human curation). A single SKOS annotation is never sufficient.
2. **Reasoner over the definitional structure, never `rdfs:subClassOf+`** (D21; lit §5.2; Bodenreider
   et al. [12]). NCIt's inferred graph does not materialize defined-class subsumption, so closure is
   computed from the stated `owl:equivalentClass`/`owl:intersectionOf` structure or by a real reasoner.
3. **Kept inside OWL 2 EL.** EL reasoners (ELK) classify SNOMED-scale ontologies in seconds
   (Kazakov et al. 2014); expressive DL does **not** scale to a merged multi-million-axiom graph.
   The merged *validation* ontology (NCIt defining fragment + curated bridges + upstream EL fragments)
   is profiled to EL and checked for satisfiability *before* classification; a merge that introduces
   unsatisfiability or non-EL axioms is rejected, not force-classified. Triple count (~10.8M) is a
   red herring — expressivity, not size, sets cost.
4. **Committed infrastructure (D28): ELK via ROBOT — free, local, no cloud.** The classifier is
   **ELK** (OWL 2 EL, Apache-2.0), driven by **ROBOT** (`robot reason --reasoner ELK`; the
   OBO-standard CLI, free) from the Python data-build/validation harness (#73). ELK classifies
   SNOMED-scale (~300K-class) ontologies in seconds and is the reasoner Uberon/CL/Mondo themselves
   ship with, so EL-profiled NCIt+upstream is comfortably in budget. Run locally on the M4 Max
   (128 GB; JVM `-Xmx32g` — ELK needs single-digit GB); **no AWS required**. Full-DL fallback for any
   EL-escaping fragment: **Konclude** (LGPLv3, free). **Cost: $0** — commercial engines (RDFox/Stardog/
   GraphDB) do Datalog/OWL 2 RL materialization, not EL classification, so are not applicable. If for
   any reason the reasoner path is deferred, success criteria 3/5 (§12) fall back to a **structural
   check over the materialized `owl:equivalentClass`/`intersectionOf` definition** (D21's fallback),
   which needs no live reasoner. Shipping a "reasoner-validated" claim without this committed
   infrastructure is forbidden. Expect inferred ≠ intended subsumption regardless (Bodenreider et al.;
   ~51% of even DL-native clinical classes carry no differentia): the reasoner is a QC/error-detector,
   **not** ground truth for equivalence.

This gate stands between every `skos:closeMatch`/candidate and any `owl:equivalentClass` bridge, and
between the bridge and `--emit-equivalence` cross-product emission.

---

## 5. Per-axis integration plan

Each NCIt `op:` axis is bound to an upstream range. The axis (the univocal relation) is NCIt-native
and stays; only its *fillers* gain upstream equivalents.

| `op:` axis (NCIt) | Upstream range | Mapping source | Relation | Notes / prior decision |
|---|---|---|---|---|
| `op:PrimarySite` (R101) | **Uberon** anatomical entity | NCIm CUI + Uberon xref | `exactMatch` / `located_in` | D23 organ-level principle → maps cleanly to Uberon organ classes. Uberon `part_of` may now break the region-vs-organ ties D20/D16 left open (§11 revisit). |
| `op:MetastaticSite` (R102) | **Uberon** | as above | `located_in` | First-class axis per D23. |
| `op:AssociatedSite` (R100) | **Uberon** | as above | `located_in` | Non-primary/non-metastatic. |
| `op:CellOrigin` (R115, normal cell) | **Cell Ontology** | CL xref + lexical | `derives_from` | The feedback's CL role. Optional axis (D23 `May_Have_*`). |
| `op:CellType` (R105, abnormal cell) | **SNOMED** morphologic abnormality + **ICD-O-3** morphology | NCIm CUI (candidate) | `derives_from` (RO bridge, **not** identity) | Histology axis. **Category caution (M1):** an NCIt *cell* (e.g. C36825 Neoplastic Neuroendocrine Cell), an ICD-O-3 *morphology/behaviour* code, and a SNOMED *morphologic-abnormality* entity are three different ontological kinds; the link is a typed RO bridge, never `closeMatch`/`exactMatch`. |
| `op:Morphology` (from taxonomic parent) | **SNOMED** morphologic abnormality + **ICD-O-3** morphology | NCIm CUI (candidate) | `closeMatch`→`exactMatch` only via §4.4 | Morphology-from-parent query still owed (engine §6, roadmap §2.2) — build it as part of this work. Morphology↔morphology *can* be identity; cell↔morphology cannot. |
| `op:MolecularAbnormality` (R106) | NCIt-native (+ optional HGNC/SO) | — | — | Kept per D23 (PR/ER/HER2 textbook case). No mainstream OBO substitute needed; oncology-specific. |
| `op:StageSystem` / `op:StageValue` (R88) | **NCIt-native** | — | — | AJCC staging is oncology-specific; NCIt's unique contribution, no upstream equivalent. Axis names per D23. |
| Regimen (`Chemotherapy_Regimen_Has_Component`) | **NCIt-native** (components → RxNorm/ChEBI optional) | RxNorm | `relatedMatch` | Secondary scope (regimen doc). Oncology-specific packaging. |
| Disease **genus** (taxonomic parent) | **Mondo / DO** | Mondo NCIt xref | `exactMatch` | Turns the cross-product's genus into a shared disease node. |

**Scope discipline (unchanged):** gene/protein role families remain excluded (assessment §3.3). No
upstream integration for `Gene_Plays_Role_In_Process` et al.

---

## 6. caDSR preservation (guaranteed) and enrichment (measured, not "free")

The hard requirement is two claims that must be separated:

**Claim 1 — preservation — is guaranteed by construction.** caDSR CDEs anchor to NCIt concept IRIs;
the NCIt reference plane is never mutated, so every existing CDE cross-link resolves exactly as it
does today. No caDSR schema change, no CDE mutation, no re-anchoring. This is additivity (D4) and is
unconditional.

**Claim 2 — upstream reach — is NOT "for free" and must be enumerated and measured.** The earlier
draft's "transitive, by construction" framing was wrong (red-team C1), because the set of NCIt
concepts caDSR anchors to is *not* the set of role-target atoms the decomposition maps. Against the
actual caDSR read model (`ontolib/.../repositories/cadsr`), a CDE anchors NCIt at three surfaces,
**most of which are outside the role-target-filler set**:

| caDSR anchor surface (ISO/IEC 11179) | What it references in NCIt | In the ~20K role-target set? |
|---|---|---|
| `ConceptLink` on Object Class / Property / Data Element Concept (`concept_type`, `is_primary`) | the **role-*bearing*** (often pre-coordinated) concept + property concepts | Only incidentally |
| `PermissibleValue.meaning_code` (value-domain value meanings) | **qualifier/value concepts** — *Grade 1/2/3*, *Positive/Negative*, laterality, units | **No** — assessment §3.4 confirms these are *not* modelled as role fillers |
| Any CDE outside the disease/neoplasm scope gate | demographics, labs, procedures, drugs, AEs | **No** — excluded by the inherited scope gate (§5) |

A caDSR component may also be **post-coordinated** — an ordered *list* of concept codes (object class
+ qualifiers) with no internal relations (Jiang et al. 2011). Coverage for such a component holds
only if **every** code in the list is mapped, not ≥1 — so coverage is computed at the *component*
level.

**The systematic mechanism (replaces "for free"):**

1. **Enumerate the caDSR anchor set.** Query the caDSR read model for the *distinct* NCIt concept
   codes referenced across **all** `concept_type`s **and all** `permissible_value.meaning_code`s of
   in-scope CDEs. This is the true mapping target for the caDSR guarantee — call it `C_cadsr`.
2. **Normalize stale anchors first.** Resolve retired/merged/duplicate codes via the EVS
   retirement/merge maps and NCIm before mapping (the first hop breaks silently otherwise; prevalence
   is operationally known but unquantified in the literature — measure it here).
3. **Map `C_cadsr ∪ C_roles`, not just `C_roles`.** The value/qualifier concepts in `C_cadsr \ C_roles`
   (grade, laterality, yes/no, units) need their **own** mapping workstream — they have no anatomy/
   cell/morphology axis in §5. Route them to the appropriate substrate (e.g. laterality → NCIt/HL7
   qualifier value sets or SNOMED qualifier hierarchy; grade → NCIt-native, no faithful OBO substitute).
4. **Report coverage as a first-class metric,** per §12: the fraction of in-scope CDEs for which every
   NCIt code on their DEC **and** relevant value meanings is (a) a live code and (b) present in the
   mapped set with an identity-grade link — broken out by single-code vs post-coordinated components,
   by anchor-liveness, and by predicate strength.
5. **`$translate` closes the loop where a mapping exists.** `ConceptMap.$translate` (FHIR; declare
   R4 `equivalence` vs R5 `relationship` — the vocabularies differ) converts NCIt↔upstream, returning
   the **honest predicate** (equivalent / broader / narrower / unmatched), never silently implying
   identity. A CDE with no identity-grade upstream link returns `unmatched`, not a fabricated answer.

This is the difference between *asserting* "caDSR maps to upstream" and *proving* it with a published
coverage number. §13 specifies the workstream and its acceptance test.

---

## 7. Governance of the dual-canonical model

The user accepted the higher governance cost of dual-canonical; this section bounds it — and corrects
two over-optimistic claims the red-team flagged (H3, H4).

- **Authority-per-stage governs authoring, not truth-over-time.** The §3.2 table says which plane you
  *author in*; it does **not** prevent a *frozen mapping from becoming false* when an endpoint moves
  (the earlier "cannot drift into contradiction" claim was a non-sequitur). Two real drift modes must
  be handled explicitly:
  - **Release-skew falsification.** `exactMatch(C12400, UBERON:0002046)` asserted against NCIt 26.02d
    + Uberon rX becomes false if Uberon splits thyroid into lobes or NCIt re-scopes C12400. Version
    pinning (below) detects *that a version changed*, not *that a specific mapping became false* — so a
    bump must **re-run §4.4 validation over the affected mapped set**, not merely fail the build. Every
    SSSOM record carries both endpoint versions so the affected set is computable from a release diff.
  - **Dual-identity duplication.** An expression authored upstream and `$translate`d to NCIt for caDSR
    storage could resolve to a *different* NCIt code than a legacy CDE's anchor — reintroducing the very
    concept-duplication the project exists to kill. Rule: `$translate` into the NCIt plane must return
    the **legacy anchor when one exists** (prefer the existing code over minting/選a sibling), and flag
    a divergence for curation when the reasoner disagrees.
  - **Mapping lifecycle states** (new): a mapping is `proposed → validated → {active | quarantined |
    retired}`. An endpoint version bump moves affected `active` mappings to `quarantined` until
    re-validated. `$translate` never serves `quarantined`/`proposed`.
- **Mapping is versioned and reviewed** like decomposition (D23 governance; `concept_xref`/SSSOM). A
  mapping is a curated assertion, not a scrape; NCIm volume is *not* evidence of correctness (M5).
- **Upstream version pinning** parallels the NCIt build pin (D5): all endpoints pinned; a bump triggers
  re-validation (above), not just a loud fail. Expect ~6–10% mapping error re-injected per upstream
  release even with best-in-class automated adaptation (Groß et al. 2016; Dos Reis et al.) — this is a
  **standing maintenance obligation**, budgeted separately (§10, LOE), not a one-time ingest.
- **Licensing gates the *serving* surface, and the "identifiers only" safety must be legally confirmed,
  not assumed (D26).** SNOMED CT is affiliate-licensed (UMLS Appendix 2 / Category 9); a table pairing
  NCIt with SCTIDs, and any SCTID/ICD-O-3 code served through a *public* `$translate` to unlicensed
  downstream users, may itself require affiliate compliance — the identifier-in-a-map can be the
  licensed artifact, and ICD-O-3 morphology codes are WHO-copyrighted *content*, not bare identifiers.
  Therefore: (a) obtain a **written license determination** before building the gated path; (b) gate the
  **serving** endpoint by consumer entitlement, not just the build flag; (c) Uberon/CL/Mondo (CC-BY/CC0)
  carry a **complete default product** with zero licensed dependency, so the open path is never blocked
  on the licensed one.

---

## 8. Code & architecture changes

Concrete, mapped onto the existing `keep-names` layout (ARCHITECTURE.md). All additive.

### 8.1 `ontolib` — storage & terminologies

- `ontolib/src/ontolib/terminologies/uberon/` — **exists** (store at :7879). Add an `xref` query
  surface (NCIt↔Uberon) and a `part_of` transitive helper for §11's tie-break revisit.
- New `ontolib/src/ontolib/terminologies/cl/`, `.../snomed/`, `.../icdo3/`, `.../mondo/` — thin
  read/xref modules, or (preferred) a **single generic `terminologies/xref/`** module parameterized
  by source, to avoid five near-duplicate packages. Decision left to implementation; generic is
  favored (mirrors the "one mapping-ingest framework" option).
- `ontolib/src/ontolib/decomposition/` — extend the `Constituent`/axis model so each filler can carry
  an optional `upstream_xref` set; extend `--emit-equivalence` to bind fillers to upstream ranges in
  the cross-product (D19 seam, now upstream-aware).
- Reserved vocab in `vocab.py`: add `op:` axis domain/range declarations referencing upstream, plus
  the `skos:*Match` / RO bridge predicates.

### 8.2 Named graphs (Oxigraph)

Add additive named graphs alongside `ncit_decomposed`: `ncit_upstream_xref` (single graph, source
tagged) **or** per-source graphs (`uberon_xref`, `cl_xref`, `snomed_xref`, `icdo3_xref`, `mondo_xref`).
Single-graph-with-provenance-tags is favored for simpler `$translate` queries. Source ontologies
that must be loaded for reasoning (Uberon already is; CL/Mondo are small) get their own read-only
graphs; SNOMED/ICD-O-3 are **not** bulk-loaded (licensing) — only the NCIt→code map is stored.

### 8.3 PostgreSQL

New tables mirroring the `decomp_*` provenance model: `concept_xref` (ncit_iri, target_iri,
target_source, skos_relation, ro_relation, umls_cui, confidence, review_status, run_id) and
`xref_run` (provenance, upstream version pins). Alembic migration `0004_xref.py`.

### 8.4 `backend` — API

- `GET /concept/{id}/mappings` — all upstream equivalents for an NCIt concept (and reverse).
- `POST /terminology/$translate` — FHIR-style ConceptMap translate, both directions, honoring SKOS
  relation and confidence.
- Extend the existing concept-detail response to include `mappings` (feature-flagged for the
  licensed sources).

### 8.5 `frontend` — SvelteKit

- Concept-detail panel: an "External mappings" section (Uberon/CL/SNOMED/ICD-O-3/Mondo links,
  relation + confidence badges).
- Graph explorer: optional overlay rendering an NCIt atom's upstream equivalent as a linked node
  (the "dual-plane" view), off by default.

### 8.6 CLI / data build

- `scripts/data_build.py` — add an `xref` stage (ingest NCIm/Mondo/Uberon/CL maps → validate →
  persist), gated by license flags for SNOMED/ICD-O-3.
- `pdm run decompose --emit-equivalence` — now emits upstream-bound cross-products when the xref
  graph is present.
- New `pdm run map` (or `data-build xref`) — the mapping ingest/validation entry point.

---

## 9. Phased sequencing (revised critical path)

The existing critical path (#4 → #9 → #5 → #6) is preserved; external integration is **inserted as a
parallel enabling track that gates goal-4 interoperability**, not as a replacement. Relation quality
still gates coverage (D22) — mapping does **not** jump ahead of the `op:` genus-sense work.

```
Phase A (foundation, parallel to #44 curation)
  A1  Generic xref ingest framework + concept_xref schema + named graph   [new epic]
  A2  Uberon + CL mapping ingest (open licenses) over the ~20K atom set    [#44-adjacent]
  A3  DL-validation harness for cross-ontology maps (D21-compliant oracle) [gates all Match promotion]

Phase B (bind to decomposition)
  B1  Attach upstream_xref to op: fillers in ncit_decomposed              [depends A2, #44 op: axes]
  B2  Uberon part_of tie-break revisit for R101 region/organ (D16/D20)     [research spike]
  B3  Mondo disease-genus mapping → cross-product genus                    [depends A1]

Phase C (morphology + licensing)
  C1  NCIm-driven SNOMED + ICD-O-3 morphology mapping (license-gated)      [op:CellType/op:Morphology]
  C2  Morphology-from-parent query (owed since engine §6)                  [enables op:Morphology]

Phase D (serve + interop)
  D1  /concept/{id}/mappings + $translate endpoints                        [depends A1]
  D2  Frontend external-mappings panel + dual-plane overlay
  D3  caDSR transitive-reach validation (CDE → NCIt → upstream resolves)   [proves §6]

Phase E (grammar, folds into #6)
  E1  MRCM ranges reference upstream classes; SCG/ECL over dual-canonical  [#6 design starts here]
  E2  cross-product --emit-equivalence upstream-bound, reasoner-validated  [#6 / D19 seam]
```

**Gating rules:** A3 gates every `exactMatch` promotion (no unvalidated equivalence ships). B precedes
C (open-license anatomy/cell before licensed morphology). E (#6) still waits on trustworthy
decomposed data (#44) *and* Phase B; D22's "relations before coverage" sequencing is unchanged —
mapping enriches the `op:` axes, it does not substitute for de-overloading them.

---

## 10. Risks & mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| **SNOMED/ICD-O-3 licensing** limits redistribution | High | D26: license-gate those sources; store only NCIt→code maps (NCIm/UMLS-compatible), never bulk upstream content; Uberon/CL/Mondo (open) carry the default experience. |
| **Mapping quality** — UMLS co-occurrence ≠ equivalence; OBO xrefs imperfect | High | §4.2 honest SKOS relations + §4.4 DL validation; golden mapping set with ≥0.9 gate; downgrade unvalidated `exactMatch`→`closeMatch`. |
| **Cross-ontology subsumption not materialized** (D21, now ×2 ontologies) | High | Real OWL reasoner over stated structure as the oracle; never `rdfs:subClassOf+`; fail-safe = preserve, don't collapse. |
| **Governance/maintenance cost** of dual-canonical + version rot | Medium | §7 single-authority-per-stage rule; upstream version pins with loud-fail (D5 parallel); mapping review workflow (D23 parity). |
| **Scope creep** into gene/protein or into re-modeling upstream domains | Medium | Hold the assessment §3.3 scope gate; NCIt *maps to* upstream, never *forks/re-authors* it. |
| **Perceived re-platforming** / stakeholder confusion | Medium | Communicate the dual-canonical invariant: NCIt stays canonical-of-record; nothing existing breaks; caDSR untouched. |
| **D16 said "don't default to Uberon"** — apparent reversal | Low | §11: D16 declined Uberon as a *tie-break default for most-specific-filler*; using it as an *xref + interop target* (and re-testing its `part_of` for the residual region/organ ties) is a different, complementary use — not a reversal. **Do not over-claim** `part_of` as an equivalence arbiter (OAEI large-bio shows partonomy alignment still yields false positives); it disambiguates the anatomy axis only. |
| **caDSR value/qualifier concepts unmapped** (grade, laterality, yes/no are outside role-target set + scope gate) | **High** | §6/§13: enumerate the true caDSR anchor set `C_cadsr` (all `concept_type` + all `permissible_value.meaning_code`), map `C_cadsr ∪ C_roles`, give value/qualifier concepts their own workstream, and report coverage — do not claim transitive reach for concepts never mapped. |
| **Reasoner intractable / non-EL merge** at 10M+ triples | **High** | §4.4/D28: profile the merged validation ontology to OWL 2 EL, check satisfiability before classifying, commit tool/profile/runtime/owner — or downgrade to the materialized-definition structural check. Triple count ≠ complexity. |
| **Mapping maintenance debt undercounted** (multi-endpoint release cadence) | **High** | §7/§10 LOE: a standing per-release re-validation obligation (~6–10% error re-injected/release); budget a separate mapping LOE, not folded into the decomposition ~5–8 pm. |
| **No interface/curation layer** for the dual-plane exposure | Medium | §8.5/§13: add an interface-terminology curation workstream; badge relation strength in the API contract (not just UI). The project's own lit review §7.4 says post-coordination fails without curated interface views. |
| **Correctness of new served surfaces** (`$translate`, `/mappings`) is not covered by additivity | Medium | §12: additivity is a substrate safety property; the new surfaces need their own correctness gate against a golden mapping+translation set. |

---

## 11. Relation to existing decisions — what is reused, extended, or revisited

- **Reused unchanged:** D4/D12 (stated-OWL, additive, named graphs), D15/D19 (most-specific projection
  vs lossless record), D17/D20/D22/D23 (`op:` univocal axes — the RO relations the feedback wants),
  D19 (SNOMED relationship groups; `--emit-equivalence` seam), D22 (SCG/ECL/MRCM; FHIR `$translate`),
  D21 (DL oracle, never `rdfs` closure), D23 (governance/minting workflow, organ-level R101).
- **Extended:** D21's DL-oracle rule now spans cross-ontology maps (D28, non-circular + EL-profiled);
  D22's grammar ranges now reference upstream; D23's minting/review workflow now also governs mappings
  (as SSSOM records). The assessment's "atoms already exist" finding gains a *qualified* sibling —
  **candidate xrefs exist in volume** (NCIm/Mondo/OBO), but upgrading them to inference-grade
  `owl:equivalentClass` is curation-grade work (D29; §14.2), not a free harvest.
- **Corrected (D27–D29, post-review):** the caDSR guarantee is enumerate-then-measure, not "for free"
  (D27); mapping validation is non-circular and reasoner-committed (D28); mapping lifecycle, economics,
  and licensing are made honest (D29). See §14 for the evidence base.
- **Revisited (honestly):** **D16** declined building a Uberon cross-check *as the default fix for
  R101 most-specific-filler ties*, because only 1 of 4 residual ties looked like a Uberon win. This
  plan does **not** overturn that finding for tie-breaking. It re-opens Uberon in a *different* role —
  equivalence mapping and interoperability substrate — and schedules a **scoped** re-test (B2) of
  Uberon `part_of` against exactly the region-vs-organ ties D20 left to a filler-semantic-type
  heuristic, since Uberon's containment is richer than NCIt's sparse `R82`. If B2 fails to help, the
  D16/D20 status quo stands; nothing regresses.

---

## 12. Success criteria

**Preservation (guarantees):**
1. Every existing NCIt concept and caDSR CDE resolves exactly as before — zero regressions (additive;
   substrate-level). Verified by a differential query snapshot pre/post.

**Coverage (measured targets, not guarantees):**
2. Publish the **caDSR anchor-coverage number** (§6/§13): fraction of in-scope CDEs whose every NCIt
   anchor (all `concept_type` + all `permissible_value.meaning_code`, post-coordinated lists counted
   whole) is a live code present in the mapped set with an identity-grade link — broken out by
   component type, anchor-liveness, and predicate strength. This *is* the systematic caDSR-mapping
   deliverable; the number is the artifact, and a target (not a claim of totality) is set from the
   Phase-A enumeration.
3. Report `owl:equivalentClass`-bridge precision against a **curated golden mapping set** (whose
   construction is a costed workstream, §13), with `needs_review` excluded (D21). State the *raw
   candidate precision before curation* alongside the post-curation number — do not present the
   curated figure as if raw NCIm xrefs achieved it.

**Correctness of the new surfaces (distinct from additivity):**
4. `$translate` returns the **honest FHIR predicate** (equivalent/broader/narrower/unmatched; declare
   R4 vs R5) for a held-out translation test set; `unmatched` where no identity-grade link exists —
   never a fabricated equivalence.
5. `--emit-equivalence` cross-products validate by the §4.4 **non-circular** gate (curated
   `owl:equivalentClass` bridge, EL-profiled reasoner *or* materialized-definition structural check) —
   **not** by feeding SKOS annotations to a reasoner, and **not** via `rdfs:subClassOf+` (D21/D28).

**Scope discipline:**
6. NCIt-native oncology axes (`op:StageSystem`, `op:MolecularAbnormality`, regimens) and caDSR
   grade/laterality/units value concepts remain first-class and are **explicitly** left unmapped where
   no faithful upstream equivalent exists — recorded as such, not silently missing.

---

## 13. The systematic caDSR ↔ NCIt ↔ upstream mapping workstream (the measurable guarantee)

This section makes the user's hard requirement — *the new ontology must map to the existing NCIt
concepts used in caDSR CDEs* — concrete, systematic, and testable. It supersedes the old "for free"
framing (§6, red-team C1). Decisions **D27** (the target set) and **D28** (validation) govern it.

### 13.1 Definition of the mapping target

Let:
- `C_roles` = distinct NCIt concepts that are role-target fillers of in-scope decomposed concepts
  (~20K, assessment §3.2).
- `C_cadsr` = distinct NCIt concept codes referenced by **in-scope caDSR CDEs**, enumerated from the
  caDSR read model across **every** anchor surface. *(Codebase-verified 2026-07-11 against the
  caDSR read model; see DECISIONS D27.)* Concretely: the codes are the distinct
  `cde_concepts.concept_code` values in the caDSR **SQLite** repository (`settings.cadsr_db_path`, read via
  `CdeRepository`), partitioned by `cde_concepts.concept_type ∈ {object_class, property, representation,
  value_meaning}`. Value-domain value meanings are already first-class rows here
  (`concept_type='value_meaning'`), so they enumerate from the same single table — the
  `PermissibleValue.meaning_code` field parsed from `cde_json` is only a redundant cross-check, not the
  primary surface. The DEC is a derived grouping (separate `cde_decs` table). Post-coordinated components
  appear as multiple `cde_concepts` rows for one CDE and must be counted whole.
- **Mapping target `M = C_roles ∪ C_cadsr`.** The caDSR guarantee is about `C_cadsr`; the
  decomposition/cross-product story is about `C_roles`; they overlap but neither contains the other.

### 13.2 Pipeline (each step a testable stage)

1. **Enumerate `C_cadsr`** from the caDSR tables; partition into single-code vs post-coordinated
   components, and DEC-anchor vs value-meaning. Emit counts (this is the denominator for coverage).
2. **Liveness/retirement normalization.** For each code in `M`, resolve retired/merged/split codes to
   their current form via EVS retirement maps + NCIm; record an `anchor_liveness` status. Codes that
   cannot be resolved are reported, not silently dropped.
3. **Candidate ingest** (SSSOM) from NCIm CUIs (SNOMED/ICD-O-3), Mondo NCIt xrefs, Uberon/CL/FMA
   xrefs — each with predicate, justification, confidence, and both endpoint versions.
4. **Curation/validation** through the §4.4 non-circular gate → promote a subset to
   `owl:equivalentClass` bridges; the rest stay `closeMatch`/`broad`/`narrow`/`related` or `unmatched`.
5. **Value/qualifier workstream** for `C_cadsr \ C_roles` (grade, laterality, yes/no, units): these
   have no §5 anatomy/cell/morphology axis. Route explicitly (laterality → qualifier value set;
   grade/units → NCIt-native or SNOMED qualifier hierarchy); where no faithful substrate exists,
   record `no-upstream-equivalent` rather than forcing one.
6. **Coverage report** (§13.3) — the deliverable.
7. **Serve** via `/concept/{id}/mappings` and `$translate`, honest predicate, licensed sources gated.

### 13.3 The coverage report (the artifact that *proves* the guarantee)

A machine-generated report over in-scope caDSR CDEs:

```
caDSR-in-scope CDEs:                         N
  ...single-code components:                 n1        ...post-coordinated (multi-code):   n2
Distinct NCIt anchors C_cadsr:               A
  ...live / retired-resolved / unresolved:   a1 / a2 / a3
  ...in C_roles / new to caDSR set:          a4 / a5
Anchors with identity-grade upstream link:   e   (by target: Uberon/CL/SNOMED/ICD-O-3/Mondo)
  ...closeMatch only / broad|narrow / none:  c / b / u
CDE-level coverage (ALL anchors mapped,      COV = |{CDE : every anchor live & identity-mapped}| / N
  post-coordinated lists counted whole)
```

**Acceptance:** `COV` is *published with the release*, with a target agreed from step 1's enumeration
(not asserted as 100%). A CDE is "reachable upstream" **iff** every anchor is live and identity-mapped;
partial mappings are reported as partial. This replaces an unfalsifiable "by construction" claim with a
number a reviewer can audit and regress against.

### 13.4 Why this satisfies "systematically map to NCIt + caDSR"

- **To current NCIt concepts:** `M ⊇ C_cadsr` and the reference plane is unmutated, so every
  caDSR-used NCIt code is retained and, after step 2, resolvable even across retirements.
- **To caDSR CDEs:** the coverage report is keyed on the *actual* CDE anchor surfaces, so it measures
  the real join, including the value-domain concepts the previous draft missed.
- **"We can have more concepts":** new upstream-canonical concepts and minted axes are additive; each
  carries a mapping back to an NCIt code (or an explicit `no-NCIt-equivalent` minted-concept record,
  D23), so the back-map is total or explicitly flagged — never silently absent.

---

## 14. Evidence base and corrections (peer-reviewed review + adversarial red-team)

This revision incorporates a peer-reviewed literature pass and an independent adversarial review.
Headline corrections to the first draft:

1. **"caDSR reaches upstream for free" — withdrawn.** Replaced by §6/§13's enumerate-then-measure
   workstream. Grounds: caDSR anchors object-class/property/DEC concepts and value-meaning codes
   (ISO/IEC 11179; Covitz et al. 2003; Nadkarni & Brandt 2006; Jiang et al. 2011/2012), which are
   largely disjoint from the role-target fillers, plus post-coordinated multi-code components and
   out-of-scope CDEs.
2. **"Mappings largely already exist" — qualified.** Candidate xrefs exist in volume, but oncology
   NCIt↔ICD-O-3/ICD-10 maps are *missing and inconsistent* (Abdelhak/Jiang group, *J Biomed Semantics*
   2017, PMC5294908) and inter-terminology precision is often low; CUI co-occurrence is editorial
   synonymy, not equivalence. Upgrading candidates to `owl:equivalentClass` is curation-grade work.
3. **Round-trip circularity — fixed.** SKOS mapping properties are annotation-only; the logical bridge
   is a *separate curated* `owl:equivalentClass`, and a mapping may not be its own evidence (§4.4/D28).
4. **Reasoner reality — committed or downgraded.** EL reasoners scale (Kazakov et al. 2014, ELK);
   expressive DL over a merged 10M+-triple graph does not — profile to EL, check satisfiability, name
   the infrastructure, or use D21's materialized-definition structural check (§4.4/D28). Inferred ≠
   intended subsumption regardless (Bodenreider et al. 2007).
5. **Mapping standard — SSSOM adopted** (Matentzoglu et al. 2022) for every cross-ontology record.
6. **Import discipline — MIREOT** (Courtot et al. 2011): reference upstream terms partially, do not
   full-OWL-import Uberon/SNOMED/Mondo.
7. **Mapping rot — budgeted.** ~6–10% error re-injected per upstream release even with automated
   adaptation (Groß et al. 2016; Dos Reis et al.); §7 lifecycle states + §10 standing maintenance LOE.
8. **Licensing — serving-gated and to be legally confirmed** (UMLS Appendix 2 / SNOMED affiliate; WHO
   ICD-O-3): open Uberon/CL/Mondo carry the default product (§7/D26).
9. **NCIt OBO Edition — real but experimental.** The originating feedback's "NCIt OBO Edition" claim is
   *correct* (canonical PURL `http://purl.obolibrary.org/obo/ncit.owl`; repo
   `github.com/NCI-Thesaurus/thesaurus-obo-edition`) but OBO labels it experimental — use the EVS OWL
   release as the authoritative substrate, not the OBO edition. (The feedback's broken URLs were stale,
   not fabricated.)

### 14.1 Key references (additive to the postcoordination lit review)

- Covitz PA, et al. caCORE. *Bioinformatics* 2003;19(18):2404–12. PMID 14668224.
- Sioutos N, et al. NCI Thesaurus. *J Biomed Inform* 2007;40(1):30–43. PMID 16697710.
- Nadkarni PM, Brandt CA. CDEs for cancer research: functions and structure. *Methods Inf Med*
  2006;45(6):594–601. PMID 17149500.
- Jiang G, Solbrig HR, Chute CG. Quality evaluation of CDEs using the UMLS Semantic Network.
  *J Biomed Inform* 2011;44(S1):S78–85. PMID 21840422. — and value sets via UMLS semantic groups,
  *JAMIA* 2012;19(e1):e129–38. PMID 22511016.
- Matentzoglu N, et al. SSSOM. *Database (Oxford)* 2022;2022:baac035. doi:10.1093/database/baac035.
- Courtot M, et al. MIREOT. *Applied Ontology* 2011;6(1):1–13. doi:10.3233/AO-2011-0087.
- Mungall CJ, et al. Cross-product extensions of the Gene Ontology. *J Biomed Inform* 2011;44(1):80–6.
  PMID 20152934. · Uberon, *Genome Biol* 2012;13(1):R5. PMID 22293552. · Diehl AD, et al. Cell Ontology
  2016, *J Biomed Semantics* 2016;7:44. PMID 27377652.
- Vasilevsky NA, et al. Mondo: integrating disease terminology. *Genetics* 2025. doi:10.1093/genetics/iyaf215.
- Ghazvinian A, Noy NF, Musen MA. How orthogonal are the OBO Foundry ontologies? *J Biomed Semantics*
  2011;2(S2):S2. PMID 21624157.
- Bodenreider O, Smith B, Kumar A, Burgun A. Investigating subsumption in SNOMED CT. *Artif Intell Med*
  2007;39(3):183–95. — Kazakov Y, et al. The Incredible ELK. *J Autom Reason* 2014;53:1–61.
- Groß A, Pruski C, Rahm E. Evolution of biomedical ontologies and mappings. *Comput Struct Biotechnol
  J* 2016;14:333–40. — Abdelhak et al. Disease classification integration in oncology from the NCIt.
  *J Biomed Semantics* 2017 (PMC5294908).
- ISO/IEC 11179-3 Metadata registries. · HL7 FHIR R4/R5 ConceptMap (equivalence→relationship). · W3C
  SKOS Reference. · UMLS Metathesaurus License Appendix 2 (SNOMED CT Affiliate).

---

*Companion artifacts: DECISIONS **D24–D29** (`../DECISIONS.md`); revised **ROADMAP** critical path
(`../ROADMAP.md`); the GitHub issue drafts and updates for execution are in
[Appendix A](#appendix-a--github-issue-drafts--updates-execution-checklist-for-claude-code).*

---

## Appendix A — GitHub issue drafts & updates (execution checklist for Claude Code)

Filed 2026-07-11 as epic #70 + children #71–#84 (placeholders replaced with real issue numbers below); dependency order is explicit. Each carries acceptance
criteria phrased so an agent can verify completion. **Discipline:** every PR stays additive (no stated-OWL
mutation), every `exactMatch` passes the D21 DL oracle, scope gate (no gene/protein) holds.

### Updates to existing issues

**Issue #4 (Decomposition engine) — add comment**
> External-integration strategy adopted (design: `docs/design/ncit-external-integration.md`, DECISIONS
> D24–D26). Impact on #4: the `op:` axes are now also the binding points for upstream equivalents. No
> change to the extractor's critical path; add an optional `upstream_xref` field to the constituent
> model (non-blocking, Phase B1). `--emit-equivalence` gains an upstream-bound cross-product mode.

**Issue #44 (Extractor curation) — add comment**
> Sequencing unchanged (D22: relations before coverage). New adjacency: the ~20K role-target atoms are
> the xref surface (Phase A2). Curating the golden set now optionally records the upstream equivalent per
> filler so the mapping golden set is built alongside the decomposition golden set. Not a gate on #44's
> ≥0.9 threshold.

**Issue #9 (Read/serve surface) — add comment**
> Extend serve surface with `GET /concept/{id}/mappings` and `POST /terminology/$translate` (Phase D1).
> Additive to the existing concept-detail response; feature-flag the license-gated (SNOMED/ICD-O-3) fields.

**Issue #6 (Post-coordination grammar) — add comment**
> D24/D26: the grammar's MRCM ranges now reference upstream classes (Uberon/CL/SNOMED/Mondo). #6 design
> still starts from SCG/ECL/MRCM (D22) but sanctions over the dual-canonical range. Depends on Phase B.

**Issue #5 (Graph balancing) — add comment**
> Unchanged dependency on trustworthy decomposed data. Note: upstream mappings give balancing an external
> validation signal (Uberon/Mondo hierarchy) once Phase B lands; do not design against it until #44 data exists.

### New issues — Epic

**#70 (EPIC): NCIt as a specialization of the OBO/SNOMED substrate — dual-canonical external integration**
> Umbrella for the external-ontology bridge (design: `docs/design/ncit-external-integration.md`;
> DECISIONS D24–D29). Delivers additive NCIt↔upstream mappings (SSSOM), cross-product logical
> definitions, dual-canonical `$translate`, and a **published caDSR coverage report**, preserving
> NCIt + caDSR anchoring. Children: #71–#84. Exit: §12 success criteria met — including the
> published caDSR coverage number (§13.3), not an unfalsifiable "for free" claim.

### New issues — Phase A (foundation)

**#71: Generic xref ingest framework + `concept_xref`/`xref_run` schema + `ncit_upstream_xref` graph**
> Build `ontolib/terminologies/xref/` (source-parameterized), Alembic `0004_xref.py`, and the additive
> named graph. **AC:** schema migrates + rolls back; a stub mapping round-trips through store + Postgres;
> zero writes to the stated NCIt graph (assert in an integration test). Depends: none.

**#72: Uberon + Cell Ontology mapping ingest over the role-target atom set**
> Ingest Uberon/CL `xref` + curated lexical matches for the ~20K `op:` fillers (anatomy + normal cell).
> **AC:** ≥X% of neoplasm-branch `op:PrimarySite`/`op:CellOrigin` fillers carry an Uberon/CL mapping;
> all stored with SKOS relation + provenance; open-license only. Depends: #71.

**#73: Non-circular cross-ontology validation harness + committed reasoner infrastructure (D28)**
> Promote candidates to a **separate curated `owl:equivalentClass` bridge** (never the SKOS annotation);
> a mapping may not be its own evidence. **Reasoner = ELK (OWL 2 EL, Apache-2.0) via ROBOT** (`robot
> reason --reasoner ELK`), shelled out from the Python data-build; input profiled to OWL 2 EL and
> satisfiability-checked before classification; **Konclude (LGPLv3) fallback** for EL-escaping fragments.
> Runs locally on the M4 Max (`-Xmx32g`), no cloud. **AC:** ROBOT/ELK wired into the harness and green on
> a smoke ontology; a bridge is promoted only on independent evidence + EL-valid classification; a known
> non-equivalent pair is demoted; feeding SKOS as `equivalentClass` is rejected by a test; oracle
> documented as neither `rdfs:subClassOf+` nor the inferred graph; EL-profile + satisfiability gate before
> any classify. Depends: #71. **Gates:** all `owl:equivalentClass` bridges and `--emit-equivalence`.

**#74: Enumerate the caDSR anchor set `C_cadsr` + liveness normalization (§13.1–13.2)**
> Query the caDSR read model for distinct NCIt codes across **all** `ConceptLink.concept_type` **and all**
> `PermissibleValue.meaning_code`; partition single-code vs post-coordinated, DEC-anchor vs value-meaning;
> resolve retired/merged codes via EVS/NCIm. **AC:** `C_cadsr` enumerated with counts; overlap with
> `C_roles` reported; anchor-liveness status per code; unresolved codes listed, not dropped. Depends: #71.
> **This is the systematic caDSR-mapping foundation.**

**#76: Golden mapping set (SSSOM) + coverage-report generator (§13.3)**
> Build a curated golden mapping set (costed, not hand-waved) in SSSOM; implement the CDE-level coverage
> report (COV, counted whole over post-coordinated lists). **AC:** golden set with justification/confidence/
> versions; report emits the §13.3 block; raw-candidate precision reported alongside curated precision.
> Depends: #74, #72.

**#75: Value/qualifier concept mapping workstream (`C_cadsr \ C_roles`)**
> Map grade/laterality/yes-no/units value-meaning concepts that have no §5 axis; route to qualifier value
> sets / NCIt-native; record `no-upstream-equivalent` where none is faithful. **AC:** every in-scope value
> concept has a mapping or an explicit no-equivalent record; none silently missing. Depends: #74.

### New issues — Phase B (bind to decomposition)

**#77: Attach `upstream_xref` to `op:` fillers in `ncit_decomposed`**
> Extend the constituent/axis model; populate from #72. **AC:** a decomposed concept exposes upstream
> equivalents per axis; additive; projection unaffected when xref absent. Depends: #72, #44 `op:` axes.

**#78: Uberon `part_of` tie-break re-test for R101 region-vs-organ ties (D16/D20 revisit)**
> Scoped spike: does Uberon containment resolve the residual region/organ ties D20 routes via
> filler-semantic-type? **AC:** measured on the D16 4-concept set + D20 cases; written up as a DECISIONS
> addendum; if no improvement, D16/D20 stand (documented null result is a valid outcome). Depends: #72.

**#79: Mondo/DO disease-genus mapping → cross-product genus**
> Ingest Mondo NCIt xrefs; bind the disease genus of decomposed concepts to Mondo. **AC:** neoplasm-branch
> genus concepts carry a Mondo `exactMatch` (DL-validated); cross-product genus renders. Depends: #71, #73.

### New issues — Phase C (morphology + licensing)

**#80: NCIm-driven SNOMED + ICD-O-3 morphology mapping (license-gated)**
> Map `op:CellType`/`op:Morphology` fillers via NCIm CUIs. **AC:** behind a license build-flag; stores only
> NCIt→code maps (no bulk upstream content); SKOS relations honest (mostly `closeMatch`); off by default.
> Depends: #71, #73. **Note:** requires SNOMED affiliate + WHO ICD-O-3 license confirmation (D26).

**#81: Morphology-from-parent query (owed since engine §6)**
> Implement the `op:Morphology` axis query (taxonomic-parent morphology). **AC:** morphology axis populated
> for the neoplasm branch; unit + integration tests. Depends: none (unblocks #80's ICD-O-3 binding).

### New issues — Phase D (serve + interop)

**#82: `/concept/{id}/mappings` + FHIR-style `$translate` endpoints + frontend panel**
> Serve mappings both directions; frontend external-mappings panel + optional dual-plane graph overlay.
> **AC:** `$translate` round-trips NCIt↔upstream; license-gated fields feature-flagged; frontend renders
> relation+confidence badges. Depends: #71, #77.

**#83: caDSR coverage report (not a sample walk) — the published guarantee**
> Generate the §13.3 coverage report over **all** in-scope caDSR CDEs (not a sample), keyed on the true
> anchor surfaces (`concept_type` + `permissible_value.meaning_code`), post-coordinated lists counted whole.
> **AC:** `COV` published with the release; partial mappings reported as partial; regression-tracked. Depends:
> #74, #76, #77, #82. **Supersedes the withdrawn "for free" claim.**

### New issues — Phase E (grammar, folds into #6)

**#84: MRCM ranges over upstream + upstream-bound `--emit-equivalence` cross-products**
> Grammar sanctioning references upstream ranges; `--emit-equivalence` emits reasoner-validated
> cross-products. **AC:** a sanctioned post-coordinated expression validates against upstream ranges;
> emitted cross-product round-trips to the source NCIt concept (D19/D21). Depends: #79, #82, #44 threshold.
