# NCIt ↔ caDSR ↔ NAACCR — relationship, strategy, and tactics

**Status:** Strategy of record for the cancer-registry touchpoint · **Author:** Hannes Niedner · **Date:** 2026-07-14
**Decision:** `docs/DECISIONS.md` **D40** · **Vision:** `README.md` ("Serving the downstream NCI ecosystem")
**Series:** first of the downstream-program relationship docs under `docs/ecosystem/`. Planned siblings —
NCI Clinical Trials Reporting Program + oncology trials in ClinicalTrials.gov; NCI CRDC; NCI CCDI.
Depends on / extends: [`../design/ncit-external-integration.md`](../design/ncit-external-integration.md)
(the dual-canonical mapping layer, §13 caDSR coverage) and DECISIONS **D24–D29, D27, D38**.

---

## 1. Why this document exists
Cancer registries are a critical consumer of oncology terminology, and a modernized NCIt must be
**usable by — and straightforwardly mappable to — registry data collection.** But "map NCIt to NAACCR"
is the wrong frame, and getting the frame wrong risks importing NAACCR's legacy modeling debt into NCIt.
This document fixes the frame, names the concrete touchpoint (**caDSR**), and states the strategy and
tactics so that DECISIONS/README can reference it in one line each.

## 2. The chain, concretely
```
CRStar (and peer registry-abstraction apps)          # collect + export cancer cases
        │  NAACCR data-exchange record / XML
        ▼
NAACCR Data Standards & Data Dictionary (v26, 2025)  # data items + value domains + rules
        │  registered as CDEs (caDSR carries NAACCR standards "11–16")
        ▼
caDSR CDEs  ── question (DEC) + value domain (permissible values / value meanings) ──┐
        │  each administered item annotated with NCIt concept codes (EVS)            │  ← THE HINGE
        ▼                                                                            │
NCIt (decomposed op: axes)  ── mapped, dual-canonical ──►  Uberon / CL / Mondo / SNOMED / ICD-O-3
```
**caDSR is the load-bearing hinge, and it is the one part of this chain already in the codebase**
(`ontolib/.../repositories/xref/cadsr_anchors.py`, `coverage.py`, the published `COV`). caDSR is "the
primary means by which cancer researchers describe … standard datasets for cancer registries (NAACCR
and SEER)," and caDSR II annotates each CDE's semantics **and** value domain with NCIt concepts.

## 3. The frame: NAACCR is four layers; NCIt absorbs only one
NAACCR bundles (1) a **reference/value-set layer** (what a data item means + allowed codes — much of it
ICD-O-3 topography/morphology, grade, SSDIs, EOD, biomarkers), (2) an **exchange standard** (record
layout / NAACCR XML), (3) an **operational rule set** (reportability, Solid Tumor / Hematopoietic rules,
multiple-primary and consolidation logic, the SEER/NAACCR edits engine, Summary Stage / EOD derivation),
and (4) **governance + legal mandate** (SEER, CDC NPCR, CoC, CCCR; reporting is statutorily required).

**Only layer (1) is terminology.** A modernized NCIt can absorb it; layers (2)–(4) are not ontology and
must never live in NCIt. So: **a new NCIt can make a dedicated NAACCR *vocabulary* largely unnecessary
(NAACCR items bind to NCIt instead of a bespoke code list); it cannot and should not replace NAACCR the
standard, the rule engine, or the organization.**

## 4. Strategy: NCIt as reference backbone, NAACCR as a profile over the shared substrate
The end state mirrors the ICD-11 **Foundation vs linearizations** pattern and the reference-vs-interface
terminology distinction (lit review §2.3, §4.3): **NCIt (decomposed, grounded in Uberon/CL/Mondo, mapped
to SNOMED/ICD-O-3) is the reference terminology; a FHIR/mCODE-modernized NAACCR is a registry-specific
linearization + binding + rule set over that shared substrate** — not a competing vocabulary. This rides
the convergence registries are *already* adopting (NAACCR Volume V electronic reporting → HL7 FHIR;
mCODE; MedMorph Central Cancer Registry Reporting; the FHIR Cancer Pathology Data Sharing IG with SNOMED
International), rather than proposing a parallel NCIt-native registry standard.

## 5. Tactics (measurable, additive, tied to existing code and issues)
1. **Scope the *existing* caDSR-coverage machinery to the NAACCR/SEER CDE subset.** The NAACCR-mappability
   number is `COV` (design §13.3) with a NAACCR-CDE filter — a predicate over `cadsr_anchors` /
   `coverage.py`, **not** a new integration. Deliverable: a published *NAACCR-scoped coverage number*.
2. **Registry mappability lives in the value-meaning layer → its critical path is #75, not the filler
   work.** A registry data item (grade, ER/PR/HER2, behavior, SSDIs, EOD) is a value domain of enumerated
   permissible values, each anchored to an NCIt value-meaning code. Per **D27/§13.3** those are exactly
   the `C_cadsr \ C_roles` anchors the project has **not** mapped yet — the **#75** value/qualifier
   workstream. Registry/NAACCR coverage ≈ #75 scoped to registry CDEs. This is why registry `COV` reads
   ~0 today, and it fixes the dependency: the registry objective rides on #75, not on #77/#78/#79.
3. **Seed the registry *profile* (the linearization) from caDSR contexts.** A caDSR CDE bundles the
   question + value domain + context/classification (SEER, NAACCR, CTEP…). The goal-4 ECL/MRCM sanctioned
   reference set can be *seeded from* the NAACCR-registered CDE collection rather than authored.
4. **Run a tri-partite, owner-attributed gap loop — this is what prevents "poisoning the well."** Mapping
   NAACCR→caDSR→NCIt surfaces three distinct, attributable gaps: **(a)** a NAACCR item with no clean caDSR
   CDE (NAACCR/caDSR curation); **(b)** a caDSR CDE with a missing/retired/low-quality NCIt value-meaning
   annotation (caDSR quality — the Jiang value-set audits are real); **(c)** an NCIt concept that cannot
   express a distinction a registry needs (falsifiable NCIt gap, Stage-4-style). You never import a NAACCR
   code into NCIt; you map *through* caDSR with honest SSSOM predicates (D25) and attribute each gap.
5. **Anti-poison via decomposition + liveness.** Where a NAACCR-linked caDSR CDE anchors to a *legacy
   pre-coordinated* NCIt code, map it to the **decomposed `op:` representation**, not the flat code —
   refactoring NAACCR's flatness and NCIt's pre-coordination at the same touchpoint. `check_liveness`
   already flags *any* retired/merged NCIt code (it is a generic per-code liveness ASK with no
   NAACCR-specific logic), so a registry CDE anchored to a dead code is caught for free.
6. **Preserve edition-specific staging; never equate v7/v8 (D39 / #132).** Registry data is inherently
   edition-versioned (Derived AJCC 7th/8th fields, SSDIs tied to an edition), and the AJCC 8th is not a
   re-print of the 7th — stage migration means a v7 "Stage III" and a v8 "Stage III" describe different
   populations. So the registry mapping factors the staging edition out as a first-class axis (D23) and
   *links* edition/finding variants without equating them (**D39**; implemented by **#132**) — this is a
   concrete registry instance of the non-collapse rule, and the reason registry staging must never be
   deduplicated across editions.

## 6. What NCIt does NOT take on (over-reach guardrails)
- **Operational rules stay in NAACCR.** Reportability, multiple-primary, consolidation, and the edits
  engine are *algorithms over coded data*; some might one day be MRCM/ECL/SHACL constraints, but promising
  NCIt subsumes the SEER edits engine or the Solid Tumor Rules is over-claiming. Open research question,
  not a deliverable.
- **Governance and mandate cannot be absorbed.** Registries report because law requires it, to bodies with
  their own authority. NCIt supplies semantics; it does not acquire NAACCR's standing.
- **Expressibility ≠ adoption.** A perfect binding matters only if SEER/NPCR/CoC adopt it — which is why
  riding the existing FHIR/mCODE path beats a parallel NCIt-native registry standard.

## 7. Risks / caveats (carry into any coverage claim)
- **caDSR currency vs NAACCR v26.** caDSR registers NAACCR "11–16"; NAACCR is at **v26 (June 2025)**,
  revised annually (new/changed SSDIs, EOD). caDSR may lag — a *measured* gap, not an assumption.
- **caDSR value-meaning annotation quality is uneven** (Jiang JAMIA audits). The registry `COV` partly
  measures caDSR's own annotation completeness; report it as such.
- **Licensing sits inside the value sets.** Registry value domains embed ICD-O-3 (WHO) and, increasingly,
  SNOMED (affiliate) codes — the **D26** licence boundary applies at the value-meaning layer too.
- **Mapping rot is worse here** (annual NAACCR/SEER cycles) — a standing maintenance obligation (**D29**).

## 8. Success criteria (measured, per project discipline)
1. A **published NAACCR-scoped `COV`** over the NAACCR/SEER caDSR-CDE subset, broken out by
   anchor liveness, single vs post-coordinated, and predicate strength — with `role_codes` wired.
2. A **tri-partite gap report**: counts of NAACCR-no-CDE / caDSR-annotation-gap / NCIt-cannot-express,
   each attributable, none conflated.
3. Registry value-meaning coverage reported as the **#75-scoped** number, not folded into the anatomy/cell
   filler coverage.
4. No NAACCR code imported into NCIt; every registry mapping expressed additively over the decomposed
   `op:` representation with an honest SSSOM predicate.

## 9. References
- NAACCR Data Standards & Data Dictionary (Vol II), v26 (2025-06): https://apps.naaccr.org/data-dictionary/ · http://datadictionary.naaccr.org/
- NAACCR Interoperability Resources (FHIR / Volume V): https://www.naaccr.org/interoperability-resources/
- CDC NPCR — Advancing Electronic Reporting (MedMorph, FHIR): https://www.cdc.gov/national-program-cancer-registries/data-modernization/advancing-electronic-reporting.html
- HL7 FHIR mCODE Implementation Guide: https://hl7.org/fhir/us/mcode/
- caDSR — Overview of Use and Collaborations (registers NAACCR/SEER): https://wiki.nci.nih.gov/pages/viewpage.action?pageId=56788919
- NCI Metadata Services for Cancer Research (caDSR II uses NCIt/EVS): https://www.cancer.gov/about-nci/organization/cbiit/vocabulary/metadata
- Current and Emerging Informatics Initiatives Impactful to Cancer Registries — PMC10229192
- Jiang G, Solbrig HR, Chute CG. Quality evaluation of value sets from cancer study CDEs using UMLS semantic groups. *JAMIA* 2012;19(e1):e129–38. PMID 22511016
- CRStar (ERS / Health Catalyst) registry abstraction + NAACCR reporting: https://mycrstar.com/crstar
- Web-based mapping from data dictionaries to ontologies, applied to cancer registry — PMC7737251
