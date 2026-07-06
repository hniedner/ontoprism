# Decomposing Pre-Coordinated NCIt Concepts — Feasibility, Strategy & Level of Effort

**Status:** Foundational assessment (the "why" behind OntoPrism) · **Author:** Hannes Niedner · **Date:** 2026-07-03
**Data basis:** Running NCIt store (Oxigraph, `localhost:7878`, inferred build **26.05d**,
12,836,426 triples) + mounted `Thesaurus.FLAT.txt` (211,957 concepts). Every headline
figure below was computed directly against that store during the analysis.

This is the empirical foundation for OntoPrism's distinctive purpose — producing a
decomposed (non-pre-coordinated) NCIt. The implementable build derived from it is the
[NCIt decomposition engine](./ncit-decomposition-engine.md) design.

---

## 1. Executive summary

Pre-coordination in NCIt is real, measurable, and largely **self-documented** by the ontology's
own role restrictions. Of 204,373 `owl:Class` concepts, **67,862 (33%) carry at least one role
restriction and 55,044 carry two or more** — the fingerprint of a single concept that fuses
several semantic axes (site + morphology + stage + finding …). The decomposition is therefore
feasible, and unusually well-supported by existing data, because **every constituent that NCIt
already models as a role filler is itself an existing, active concept**: all 20,021 distinct
role-target concepts resolve to defined `owl:Class` nodes, none deprecated. For the roles-first
path, **constituent coverage is 100% — no new concepts need to be created**.

The residual work — and the true source of effort — is threefold: (1) selecting the *most
specific* filler per axis, because the inferred store materialises the full ancestor closure;
(2) recovering the minority of axes that are pre-coordinated only in the *label* (laterality,
staging-manual version, "with/without <finding>") and are not carried as roles, which is where a
handful of new concepts must be minted; and (3) designing a backward-compatible representation
that keeps every legacy pre-coordinated code resolvable while linking it to its constituents.

Recommended scope: **the Disease/Neoplasm branch** (Neoplastic Process 16,467 + Disease or
Syndrome 5,808 + Cell/Molecular Dysfunction 3,911 concepts with roles), where pre-coordination
drives the combinatorial concept explosion. The molecular-biology role families (Gene 14,662,
Protein 9,942, Enzyme, Receptor) should be **excluded** — their roles express genuine biological
relationships, not label-level aggregation, and decomposing them yields no benefit.

**Overall verdict:** Feasible and low-risk for the decomposition *modelling*; the effort is
concentrated in filler-selection tooling, curation of the NLP-fallback long tail, and governance
of a dual (legacy + decomposed) representation. Indicative effort for a production-grade pass over
the neoplasm branch: **~5–8 person-months** (see §7), most of it curation and QA, not engineering.

---

## 2. What "pre-coordination" means in NCIt, concretely

A post-coordinated design expresses a compound clinical meaning by *combining* atomic concepts at
use time (e.g. `Small Cell Carcinoma` + `Lung` + `Stage IIIB`). NCIt instead **enumerates the
combination as one concept** and records the combining axes as OWL existential restrictions on
that concept:

```
C35756  "Stage IIIB Lung Small Cell Carcinoma with Pleural Effusion AJCC v7"
  rdfs:subClassOf [ owl:onProperty Disease_Is_Stage             ; owl:someValuesFrom C27978 "Stage IIIB" ]
  rdfs:subClassOf [ owl:onProperty Disease_Has_Primary_Anatomic_Site ; owl:someValuesFrom C12468 "Lung" ]
  rdfs:subClassOf [ owl:onProperty Disease_Has_Finding          ; owl:someValuesFrom  …   "Pleural Effusion" ]
  …morphology carried by the taxonomic parent (Small Cell Carcinoma)…
```

These role restrictions are the machine-readable decomposition NCIt already ships. The task is not
to *invent* the constituents but to *surface, normalise, and re-link* them.

---

## 3. Feasibility — the evidence

### 3.1 Prevalence (whole corpus)

| Measure | Value | Source |
|---|---|---|
| `owl:Class` concepts in store | 204,373 | Oxigraph |
| Concepts in FLAT file | 211,957 | mounted file |
| Concepts with ≥1 role restriction | **67,862 (33.2%)** | Oxigraph |
| Concepts with ≥2 role restrictions | **55,044** | Oxigraph |
| Concepts with exactly 1 role | 12,818 | Oxigraph |
| Total role restrictions | 628,772 | Oxigraph |
| Distinct role properties (R-codes) | 95 | Oxigraph |
| Concepts w/ multi-aspect *label* signal | 35,637 (16.8%) | FLAT NLP scan |

The two signals are complementary: roles capture machine-modelled aspects; the label scan catches
lexically-fused aspects (hyphens, "of the", "with", "stage/grade", parentheses) that may or may not
be mirrored in roles.

### 3.2 Constituent coverage — the decisive result

| Measure | Value |
|---|---|
| Distinct role-target concepts (candidate constituents) | 20,021 |
| …that are NCIt IRIs | 20,021 (100%) |
| …defined as `owl:Class` (i.e. real concepts) | 20,021 (100%) |
| …flagged deprecated | 0 |

**Interpretation:** for every aspect NCIt already models as a role, the constituent concept
exists and is active. The "do all constituents already exist?" question — the crux of the
user's brief — is answered **yes, for the roles-first path**. New-concept creation is confined to
the NLP-fallback tail (§3.4).

### 3.3 Where pre-coordination lives (top role types)

| Role | Count | Family |
|---|---|---|
| Disease_Has_Abnormal_Cell | 70,226 | Disease/Neoplasm ✅ target |
| Disease_Has_Associated_Anatomic_Site | 63,141 | Disease/Neoplasm ✅ |
| Disease_Has_Finding | 61,467 | Disease/Neoplasm ✅ |
| Gene_Plays_Role_In_Process | 55,563 | Molecular ❌ out of scope |
| Disease_Has_Primary_Anatomic_Site | 50,206 | Disease/Neoplasm ✅ |
| Gene_Product_Plays_Role_In_Biological_Process | 33,389 | Molecular ❌ |
| Disease_Has_Normal_Cell_Origin | 26,873 | Disease/Neoplasm ✅ |
| Chemotherapy_Regimen_Has_Component | 14,121 | Regimen ✅ (secondary) |

By semantic type, role-bearing concepts cluster as: Neoplastic Process 16,467, Gene or Genome
14,662, Amino Acid/Peptide/Protein 9,942, Disease or Syndrome 5,808, Therapeutic/Preventive
Procedure 5,309. **Scope recommendation: keep the disease/neoplasm and regimen families; drop the
gene/protein families.**

### 3.4 The NLP-fallback long tail (where new concepts appear)

Some pre-coordinated axes appear only in the label, not as roles:

- **Laterality** ("Left"/"Right"): pre-coordinated in labels but essentially *not* modelled as a
  role filler — only 2 neoplasm concepts even use Left/Right in the label with roles attached.
  Decomposing laterality requires a laterality value set (Left/Right/Bilateral); NCIt has qualifier
  concepts for these, so creation is minimal.
- **Staging-manual version** (AJCC v7 vs v8): modelled as a *stage-system* filler (`AJCC v7 Stage`
  C90530), so it is recoverable from roles — but it drives duplicate concepts (see §5).
- **"With/without <finding>"** forks: the finding exists as a concept; the negation ("without") is
  the modelling gap and may need an explicit *absent/excluded* qualifier.

Estimated new concepts required: **low hundreds**, concentrated in qualifier/value-set concepts,
not in the clinical entities themselves.

---

## 4. The inferred-closure trap (a real methodological constraint)

The on-disk build is `ThesaurusInferred.owl` — a **reasoner-materialised** graph. Two consequences
for decomposition:

1. **Ancestor bleed.** Each axis returns the asserted filler *plus every ancestor*. Querying the
   primary site of `C6135` (Stage III Thyroid Gland Medullary Carcinoma) returns Thyroid Gland,
   Endocrine Gland, Endocrine System, Head and Neck, Neck — only *Thyroid Gland* is the intended
   constituent. Decomposition must therefore select the **most specific filler** per axis (the
   hierarchy leaf), or ingest the **asserted** `Thesaurus.owl` (stated form) instead of the inferred
   build.
2. **Negative axioms.** The inferred build adds `Disease_Excludes_Abnormal_Cell` /
   `Disease_Excludes_Finding` disjointness restrictions (35,662 + 30,009 of them). These are
   **not constituents** and must be filtered out; they explain why some concepts show 80+ "roles".

**Action:** obtain/regenerate the **stated** NCIt OWL (or the OWLF "flat roles" export) for the
production pipeline, and treat the inferred store only for validation/closure checks.

---

## 5. Worked pilot — Neoplasm branch

78% of neoplasm concepts (13,195 / 16,954) carry a primary-site role, so the branch is highly
decomposable. Three exemplars (full data in `data/pilot_neoplasm.json`):

**C6135 — "Stage III Thyroid Gland Medullary Carcinoma AJCC v7"** decomposes to:
- Stage = `C27970` Stage III · Stage system = `C90530` AJCC v7 Stage
- Primary site (most specific) = `C12400` Thyroid Gland
- Abnormal cell (most specific) = `C36761` Neoplastic Neuroendocrine Cell
- Morphology = Medullary Carcinoma (taxonomic-parent axis)
- **Near-duplicate:** `C141045` = the *same clinical entity* re-enumerated for AJCC v8.

**C35756 — "Stage IIIB Lung Small Cell Carcinoma with Pleural Effusion AJCC v7"** decomposes to
Stage IIIB + AJCC v7 + Lung + Small Cell Carcinoma + Pleural Effusion, and sits in a **sibling
explosion**: `C35756` (with effusion), `C35757` (without effusion), `C6681` (unspecified),
`C6679` (Stage III parent) — four enumerated concepts for one axis of variation.

**C4791 — "Left Atrial Myxoma":** site = Heart (`C12727`), morphology = Myxoma, abnormal cell =
Neoplastic Spindle Cell (`C36954`), finding = Myxoid Stroma Formation (`C35998`); *laterality
(Left)* is label-only → NLP fallback.

These cases demonstrate both the decomposability (constituents are all present) and the payoff:
AJCC-version and with/without forks collapse into one core entity plus orthogonal qualifiers.

---

## 6. Backward-compatible representation

**Design goal:** never break an existing `Cxxxxx` reference. Every legacy pre-coordinated code
stays resolvable, keeps its label/definition/mappings, and gains explicit links to its constituents.

Proposed model (additive — no deletions):

1. **Flag, don't delete.** Add an annotation on each decomposed concept, e.g.
   `:representationStatus "legacy-precoordinated"` and `:decomposedOn "2026-…"`. The concept remains
   `owl:Class`, retains all existing axioms, so downstream systems continue to resolve it.
2. **Explicit constituent links.** Introduce one non-defining association property,
   `:hasConstituentConcept`, plus the *typed* axis it fills, so consumers can read both the flat
   list and the semantics:
   ```
   C6135 :representationStatus "legacy-precoordinated" ;
         :hasConstituent [ :axis Disease_Is_Stage             ; :filler C27970 ] ;
         :hasConstituent [ :axis Disease_Has_Primary_Anatomic_Site ; :filler C12400 ] ;
         :hasConstituent [ :axis Disease_Has_Abnormal_Cell    ; :filler C36761 ] ;
         :hasConstituent [ :axis :Morphology                  ; :filler <Medullary Carcinoma> ] .
   ```
   (Reuse existing role properties as the `:axis` value where one exists; this keeps the links
   semantically self-describing and lets the existing role restrictions remain untouched.)
3. **Create only the genuinely missing constituents** (laterality/absence qualifiers, a handful of
   value-set nodes), then link them the same way. Coverage analysis shows this set is small.
4. **Optional post-coordination equivalence.** For entities with a clean, complete axis set,
   additionally assert an `owl:equivalentClass` intersection of the fillers — this is what turns the
   legacy concept into a *derivable* post-coordinated expression and enables future de-duplication
   (AJCC v7/v8 forks become equivalent up to the stage-system qualifier).
5. **Deprecation policy:** legacy concepts are *retained indefinitely* (flagged), never retired,
   guaranteeing backward compatibility. New authoring is steered to the post-coordinated form.

This is additive and reversible: it introduces annotations and association triples only, so a
consumer that ignores them sees today's NCIt unchanged.

---

## 7. Level of Effort

Scope = disease/neoplasm/regimen families (~26K role-bearing concepts), production-grade.

| Workstream | Effort | Notes |
|---|---|---|
| Ingest **stated** NCIt OWL; build asserted-roles table | 0.5 pm | avoids inferred-closure bleed |
| Filler-selection engine (most-specific per axis, Excludes_* filtering, morphology-from-parent) | 1.0 pm | core engineering; deterministic + testable |
| NLP-fallback extractor (laterality, "with/without", version) + new-qualifier minting | 1.0 pm | long tail, curation-heavy |
| Backward-compatible model + linker (annotations, `:hasConstituent`, optional equivalentClass) | 0.75 pm | additive OWL/graph writes |
| Curation & QA (sampling, clinician review, disjointness/consistency checks in Oxigraph) | 2.0–4.0 pm | dominant cost; scales with quality bar |
| Governance, docs, release integration into fairdata pipeline | 0.5 pm | dual-representation policy, versioning |
| **Total** | **~5.75–8.25 pm** | ~70% curation/QA, ~30% engineering |

Effort is front-loaded on the branch with the highest ROI (neoplasm). A **1–2 person-week spike**
on ~200 sampled neoplasm concepts would de-risk the filler-selection heuristics and firm up the
curation rate before committing to the full pass.

---

## 8. Risks & mitigations

- **Inferred vs stated confusion** → standardise on the stated OWL for extraction; use inferred only
  for validation. (Highest-priority technical item.)
- **Most-specific-filler errors** on multi-parent anatomy → validate against Uberon (already running
  at `:7879`) and NCIt's own anatomy hierarchy; flag ambiguous cases for review.
- **Semantic loss on "excludes/without"** → model absence explicitly rather than dropping it.
- **Consumer breakage** → mitigated by construction: representation is purely additive, legacy codes
  never removed.
- **Scope creep into gene/protein roles** → hold the line; those are out of scope by design.

---

## 9. Recommendation

Proceed, scoped to the disease/neoplasm(/regimen) branch, in three phases: **(P1)** stated-OWL
ingest + filler-selection spike on ~200 concepts; **(P2)** backward-compatible linker producing the
legacy-flag + `:hasConstituent` graph for the full branch; **(P3)** NLP-fallback tail, optional
equivalentClass post-coordination, and governance. The feasibility question is settled favourably by
the data: the constituents are already in NCIt (100% for roles), and the representation can be made
backward-compatible without deleting or altering a single existing concept.

---

*Reproducibility: the headline figures were derived by SPARQL queries against the live
NCIt Oxigraph store (build 26.05d) and an NLP scan of `Thesaurus.FLAT.txt`. The raw
analysis working set (per-metric JSON dumps and the query/method checkpoint) lived under
the local, untracked `tmp/ncit-decomp/` scratch during the assessment; the numbers it
produced are reproduced inline throughout this document.*
