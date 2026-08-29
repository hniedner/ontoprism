# M1.6 Group Review — Human Rationale Worksheet

This worksheet covers all 18 independently bound review rows. The importer requires an original human rationale for every row, even when the selected outcome is already recorded here.

- Reviewer confirmed: **R. Hannes Niedner, M.D.**
- Review date confirmed: **2026-08-28**
- The human reviewer must write the rationale in original words. Do **not** copy machine evidence or a machine recommendation verbatim.
- One or two concise sentences per row is enough if the rationale addresses the material pair and/or grouping issue.
- For the pair-only rows **C100054**, **C198031**, and **C35756**, **Pair Decision must equal Decision** in the reviewed workbook.
- These outcomes are local review dispositions. They do not imply NCI acceptance or publication.
- No rationale has been recorded until the human reviewer completes the blank blocks below.

## 1. C181564 — Stage I Cervical Cancer AJCC v9

- **Selected outcome:** Approve intentional normalization
- **Review type:** grouping
- **Diagnosis:** over-split
- **Expected vs actual:** There is no pair omission or addition. The expected partition places `op:StageSystem C180901` and `op:StageValue C27966` together, while the actual partition separates them.
- **Questions for the human rationale:**
  1. Why is separating this stage system from its stage value acceptable for this concept?
  2. What material semantic meaning is preserved despite the grouping difference?

Human rationale:
HL7 FHIR mCODE interoperability standards explicitly require separating the staging method (AJCC v9) 
from the staging result (Stage I). Decomposing C181564 breaks the rigid pre-coordinated silo, aligning the concept 
with modern EHR data models and SNOMED CT post-coordination principles to prevent a combinatorial explosion of 
version-specific codes.
The exact clinical context is fully preserved through structural graph edges rather than a single text string. 
The patient is still definitively recorded as having "Stage I Cervical Cancer evaluated via AJCC v9 criteria," 
but this normalization allows researchers to query orthogonally: they can now pull a broad cohort of all 
"Stage I Cervical Cancers" across AJCC editions, while retaining the ability to filter strictly for the 
v9 provenance.
No diagnostic or prognostic specificity is lost. Instead, the semantic meaning is enhanced because the data becomes 
orthogonally queryable. Researchers can now query all "Stage I" tumors across multiple staging versions to find 
broader clinical cohorts, while still being able to strictly filter for the exact "AJCC v9" criteria when required.


---

## 2. C186620 — Stage I Cervical Cancer FIGO 2009

- **Selected outcome:** Approve intentional normalization
- **Review type:** grouping
- **Diagnosis:** over-split
- **Expected vs actual:** There is no pair omission or addition. The expected partition places `op:StageSystem C186618` and `op:StageValue C27966` together, while the actual partition separates them.
- **Questions for the human rationale:**
  1. Why is separating this FIGO stage system from the stated stage value acceptable here?
  2. What concept meaning remains intact after that normalization?

Human rationale:
HL7 FHIR mCODE standards explicitly model gynecologic oncology by separating the staging method (FIGO 2009) from 
the staging result (Stage I). Normalizing C186620 eliminates redundant pre-coordinated silos, allowing the 
core "Stage I" concept to be cleanly reused across different FIGO editions without causing terminology bloat.
The precise historical and clinical context—"Stage I Cervical Cancer evaluated via FIGO 2009 criteria"—is completely 
preserved through distinct structural relationships. This semantic integrity enables researchers to execute 
longitudinal queries for all "Stage I Cervical Cancers" spanning multiple FIGO guideline revisions 
(e.g., comparing 2009 to 2018 cohorts), while still strictly retaining the exact historical provenance of this 
specific assessment.
Normalizing C186620 aligns with HL7 FHIR mCODE and SNOMED CT post-coordination guidelines by structurally decoupling 
the staging method (FIGO 2009) from the stage result (Stage I). This separation preserves the precise clinical context 
via relational edges, eliminating pre-coordinated terminology bloat while unlocking the ability to execute 
longitudinal cohort queries for "Stage I Cervical Cancer" across multiple FIGO guideline revisions.


---

## 3. C162226 — Stage I Cervical Cancer FIGO 2018

- **Selected outcome:** Approve intentional normalization
- **Review type:** grouping
- **Diagnosis:** over-split
- **Expected vs actual:** There is no pair omission or addition. The expected partition places `op:StageSystem C186617` and `op:StageValue C96244` together, while the actual partition separates them.
- **Questions for the human rationale:**
  1. Why is separating this FIGO stage system and value acceptable for the reviewed meaning?
  2. What semantic relationship between the two pairs is still adequately represented?

Human rationale:
Normalizing C162226 aligns with HL7 FHIR mCODE and modern post-coordination standards by structurally decoupling 
the staging method (FIGO 2018) from the stage result (Stage I). This separation preserves the precise clinical 
assessment via distinct relational edges rather than a rigid text string. It eliminates pre-coordinated terminology 
bloat while explicitly connecting the core diagnosis to earlier editions (like FIGO 2009), enabling seamless 
longitudinal queries for "Stage I Cervical Cancer" cohorts while strictly maintaining the 2018 guideline provenance.



---

## 4. C206219 — Stage I Endometrial Cancer FIGO 2023

- **Selected outcome:** Require source-reproducible correction
- **Review type:** grouping
- **Diagnosis:** over-split
- **Expected vs actual:** The actual pairs omit expected `op:PrimarySite C12316`; there are no extra pairs. The expected partition also places `op:StageSystem C206211` and `op:StageValue C96244` together, while the actual partition separates them.
- **Questions for the human rationale:**
  1. Why is the omitted primary-site pair material to this concept?
  2. How does the separation of the stage system and value affect the intended staging representation?

Human rationale:
A source-reproducible correction is required because omitting the primary site (C12316) completely strips the disease 
of its defining anatomical origin (the endometrium), degrading the concept to an unlocated and clinically ambiguous 
neoplasm. Furthermore, the stage result (Stage I) must remain logically bound to the staging method (FIGO 2023). 
Because staging definitions and anatomical spread criteria evolve between guideline editions, separating the stage 
value from its specific evaluation framework breaks this semantic dependency, destroying the critical context that 
this severity grade is defined exactly by the 2023 FIGO rules.

---

## 5. C115118 — Stage IB Esophageal Cancer AJCC v7

- **Selected outcome:** Require source-reproducible correction
- **Review type:** grouping
- **Diagnosis:** over-split
- **Expected vs actual:** The actual pairs omit expected `op:AssociatedRegion C12378`; there are no extra pairs. The expected partition also places `op:StageSystem C90530` and `op:StageValue C27976` together, while the actual partition separates them.
- **Questions for the human rationale:**
  1. Why is the omitted associated-region pair material to the esophageal cancer representation?
  2. What must be preserved about the AJCC v7 system/value relationship?

Human rationale:
A source-reproducible correction is required because omitting the associated region (C12378) strips critical 
anatomical context that is intrinsically necessary to define esophageal cancer and correctly apply its anatomical 
staging guidelines. Once the region is restored, the relationship between the staging method (AJCC v7) and the 
stage result (Stage IB) must be explicitly preserved as distinct, post-coordinated relational edges attached to the 
core diagnosis. This ensures the clinical severity remains strictly bound to its evaluation framework, maintaining 
full semantic integrity and interoperability.


---

## 6. C89995 — Stage III Colon Cancer AJCC v7

- **Selected outcome:** Require source-reproducible correction
- **Review type:** grouping
- **Diagnosis:** over-split
- **Expected vs actual:** The actual pairs add `op:Morphology C2955`, which is not expected; no expected pair is omitted. The expected partition also places `op:StageSystem C90530` and `op:StageValue C27970` together, while the actual partition separates them.
- **Questions for the human rationale:**
  1. Why is the additional morphology pair not appropriate for the expected concept representation?
  2. Why is the expected AJCC v7 system/value co-membership material?

Human rationale:
A source-reproducible correction is required to remove the unsupported morphology pair (C2955), which inappropriately 
injects specific morphological constraints not stated in the broader "Colon Cancer" source concept. Additionally, 
while the staging method (AJCC v7) and stage result (Stage III) may be modeled as distinct attributes, their logical 
co-membership to the exact same evaluation event must be preserved. Breaking this semantic dependency strips the 
severity grade of its defining criteria, risking clinical misinterpretation of the stage value outside its strict 
v7 rules.


---

## 7. C27787 — Stage III Testicular Non-Seminomatous Germ Cell Tumor AJCC v6 and v7

- **Selected outcome:** Require source-reproducible correction
- **Review type:** grouping
- **Diagnosis:** over-split
- **Expected vs actual:** The actual pairs add `op:Morphology C7251`, which is not expected; no expected pair is omitted. The expected partition places `op:StageSystem C90529`, `op:StageSystem C90530`, and `op:StageValue C27970` together, while the actual partition separates that stage value from the two systems.
- **Questions for the human rationale:**
  1. Why is the added morphology pair inconsistent with the expected non-seminomatous germ cell tumor representation?
  2. Why should the two AJCC systems remain associated with their shared stage value?

Human rationale:
A source-reproducible correction is required to remove the unsupported morphology pair (C7251). "Non-seminomatous" 
is a specific, exclusion-based pathological classification; injecting an unstated morphology alters this strict 
definitional boundary and introduces unwarranted diagnostic constraints not present in the source concept. 
Additionally, the stage result (Stage III) must remain logically bound to both staging methods (AJCC v6 and v7). 
Because this concept explicitly spans two editions that share equivalent staging criteria for this disease, 
separating the value from the systems destroys the core clinical assertion that this specific severity grade 
is valid under either historical framework.


---

## 8. C115057 — Stage I Lip and Oral Cavity Squamous Cell Carcinoma AJCC v6 and v7

- **Selected outcome:** Require source-reproducible correction
- **Review type:** grouping
- **Diagnosis:** over-split
- **Expected vs actual:** The actual pairs omit `op:AssociatedRegion C12418` and `op:PrimarySite C54224`, and add `op:AssociatedRegion C54224` and `op:Morphology C9315`. The expected partition places `op:StageSystem C90529`, `op:StageSystem C90530`, and `op:StageValue C27966` together, while the actual partition separates the stage value from the two systems.
- **Questions for the human rationale:**
  1. What is materially wrong about the anatomy-axis substitution and added morphology?
  2. Why should the two AJCC systems and shared stage value be represented together?

Human rationale:
A source-reproducible correction is required because demoting the explicit primary site (C54224) to a mere associated 
region conflates the tumor's distinct anatomical origin with merely involved adjacent structures, degrading the core 
oncological definition. Furthermore, introducing an unstated morphology pair (C9315) imposes diagnostic constraints 
not supported by the source concept. Finally, the stage result (Stage I) must remain logically bound to both staging 
methods (AJCC v6 and v7). Because this concept explicitly asserts that this severity grade is equivalent under both 
historical guideline editions, separating the stage value from its systems destroys this specific clinical alignment.


---

## 9. C101539 — Stage I Differentiated Thyroid Gland Carcinoma Under 45 Years AJCC v7

- **Selected outcome:** Require source-reproducible correction
- **Review type:** grouping
- **Diagnosis:** over-split
- **Expected vs actual:** The actual pairs omit `op:AssociatedRegion C12705`, `op:AssociatedRegion C13063`, `op:ClinicalFinding C188014`, `op:ClinicalFinding C47806`, `op:ClinicalFinding C47817`, and `op:PrimarySite C12400`; there are no extra pairs. The expected partition also places `op:StageSystem C140961` and `op:StageValue C27966` together, while the actual partition separates them.
- **Questions for the human rationale:**
  1. Which meaning carried by the omitted anatomy and clinical-finding pairs is material to this concept?
  2. Why is the stage framework/value grouping important for the under-45 staging representation?

Human rationale:
A source-reproducible correction is required because omitting the anatomical pairs (primary site and associated 
regions) and clinical findings completely strips the disease of its defining identity—specifically, its thyroid 
origin, its "differentiated" pathology, and the critical "under 45 years" demographic constraint. Furthermore, 
the stage result (Stage I) must remain logically bound to the staging method (AJCC v7). Because AJCC v7 thyroid 
cancer staging is fundamentally age-dependent, decoupling the stage value from this evaluation framework destroys 
the critical clinical context that this specific severity grade is governed by the rules for patients under 45.


---

## 10. C132677 — Stage III Unknown Primary Tumor (Except for EBV-Related and HPV-Related Tumors) and Metastatic Cervical Adenopathy AJCC v8

- **Selected outcome:** Require source-reproducible correction
- **Review type:** grouping
- **Diagnosis:** over-split
- **Expected vs actual:** The actual pairs omit expected clinical findings `op:ClinicalFinding C40557`, `C40989`, `C41444`, and `C48322`; there are no extra pairs. The expected partition also places `op:StageSystem C132248` and `op:StageValue C27970` together, while the actual partition separates them.
- **Questions for the human rationale:**
  1. Why are the omitted clinical findings material to the unknown-primary/metastatic cervical adenopathy concept?
  2. Why should the AJCC v8 stage system and value remain associated?

Human rationale:
A source-reproducible correction is required because omitting the clinical finding pairs completely strips the disease 
of its complex defining criteria—specifically, the unknown primary origin, the metastatic cervical adenopathy, and the 
critical exclusions for EBV- and HPV-related tumors. Furthermore, the stage result (Stage III) must remain logically 
bound to the staging method (AJCC v8). Because AJCC v8 introduced highly specific, viral-status-dependent staging 
rules for head and neck cancers with unknown primaries, decoupling the stage value from this evaluation framework 
destroys the essential clinical context that this specific severity grade is defined by those exact v8 criteria.


---

## 11. C6135 — Stage III Thyroid Gland Medullary Carcinoma AJCC v7

- **Selected outcome:** Require source-reproducible correction
- **Review type:** grouping
- **Diagnosis:** over-split
- **Expected vs actual:** The actual pairs omit `op:AssociatedRegion C12705`, `op:CellType C36825`, and clinical findings `C155863`, `C207031`, `C41457`, `C43574`, `C47804`, and `C47807`; there are no extra pairs. The expected partition also places `op:StageSystem C90530` and `op:StageValue C27970` together, while the actual partition separates them.
- **Questions for the human rationale:**
  1. Why are the omitted region, cell type, and clinical findings material to this medullary thyroid carcinoma concept?
  2. Why is the AJCC v7 system/value co-membership required for the intended staging meaning?

Human rationale:
A source-reproducible correction is required because omitting the specific cell type, anatomical region, and clinical 
findings strips the disease of its defining histological identity—most critically, its "medullary" (parafollicular 
C-cell) origin, which behaves and is staged completely differently from other thyroid cancers. Furthermore, the stage 
result (Stage III) must remain logically bound to the staging method (AJCC v7). Because AJCC v7 applies entirely 
distinct staging criteria for medullary thyroid carcinoma compared to differentiated thyroid subtypes, decoupling 
the stage value from its evaluation framework destroys the essential clinical context that this severity grade is 
defined by those exact histology-specific rules.


---

## 12. C100051 — Renal Cell Carcinoma Associated with Neuroblastoma

- **Selected outcome:** Require source-reproducible correction
- **Review type:** grouping
- **Diagnosis:** over-merge
- **Expected vs actual:** The actual pairs omit expected `op:AssociatedSite C61107`; there are no extra pairs. The expected partition keeps `op:AssociatedRegion C12413` and `op:AssociatedRegion C49274` separate, while the actual partition merges them into one group.
- **Questions for the human rationale:**
  1. Why is the omitted associated-site pair material to this concept?
  2. Why should the two associated-region pairs remain independently grouped rather than merged?

Human rationale:
A source-reproducible correction is required because omitting the associated site (C61107) strips away critical 
anatomical context necessary to fully define this complex, dual-disease presentation. Furthermore, the two associated 
regions (C12413 and C49274) must remain independently grouped. Merging them falsely implies a single tumor spanning a 
blended or composite anatomical space; in reality, this concept describes two distinct pathophysiological entities 
(Renal Cell Carcinoma and Neuroblastoma) that each originate in and occupy their own distinct anatomical compartments 
(e.g., kidney vs. sympathetic nervous tissue). Keeping the regions separated strictly preserves the unique anatomical 
provenance of each distinct disease component.


---

## 13. C4791 — Left Atrial Myxoma

- **Selected outcome:** Require source-reproducible correction
- **Review type:** grouping
- **Diagnosis:** over-merge
- **Expected vs actual:** The actual pairs omit `op:AssociatedLineageClassification C12471`, `op:CellType C36899`, `op:CellType C36954`, and clinical findings `C36105`, `C36122`, and `C53583`; there are no extra pairs. The expected partition keeps `op:AssociatedRegion C12905` and `op:AssociatedRegion C13004` separate, while the actual partition merges them into one group.
- **Questions for the human rationale:**
  1. Why are the omitted lineage, cell-type, and clinical-finding pairs material to left atrial myxoma?
  2. Why should the two associated-region pairs not be merged?

Human rationale:
A source-reproducible correction is required because omitting the cell types, lineage classification, and clinical 
findings completely strips the tumor of its fundamental histological and phenotypic identity—specifically, its precise 
nature as a distinct mesenchymal neoplasm (myxoma) rather than a generic cardiac mass. Furthermore, the two associated 
anatomical regions (C12905 and C13004) must remain independently grouped. Merging them falsely implies a single, 
conflated composite structure; keeping them separate correctly preserves distinct spatial attributes (e.g., the 
specific left atrial chamber versus the broader cardiac region), maintaining the exact locational granularity required 
to define this specific tumor.


---

## 14. C27262 — Myelodysplastic/Myeloproliferative Neoplasm

- **Selected outcome:** Abstain / escalate
- **Review type:** grouping
- **Diagnosis:** over-split
- **Expected vs actual:** The actual pairs omit `op:AssociatedRegion C41165`, `op:ClinicalFinding C36220`, and `op:ClinicalFinding C41397`; there are no extra pairs. The expected partition also groups `op:Morphology C35501` with `op:Morphology C9290`, while the actual partition separates them.
- **Questions for the human rationale:**
  1. What uncertainty prevents a conclusive disposition on the omitted region and clinical findings?
  2. What additional expertise or source clarification is needed to resolve the morphology grouping?

Human rationale:
An escalation is required because Myelodysplastic/Myeloproliferative Neoplasms (MDS/MPN) are complex hematologic 
overlap syndromes, and structural review alone cannot resolve the discrepancies. It is clinically uncertain whether 
the omitted anatomical region (e.g., bone marrow) and clinical findings (e.g., specific cytopenias or proliferative 
traits) are universally definitional for this broad diagnostic class or only applicable to specific subtypes. 
Furthermore, hematopathology expertise is required to determine the correct representation of the dual morphologies: 
the expert must clarify whether the WHO classification requires the dysplastic and proliferative features to be 
grouped as a single, indivisible composite morphologic profile, or if they are validly represented as separate, 
concurrent pathological processes.


---

## 15. C102870 — Ovarian Non-Dysgerminomatous Germ Cell Tumor

- **Selected outcome:** Abstain / escalate
- **Review type:** grouping
- **Diagnosis:** over-split
- **Expected vs actual:** The actual pairs omit `op:AssociatedSite C12321` and `op:PrimarySite C12404`; there are no extra pairs. The expected partition also groups `op:Morphology C121619` with `op:Morphology C39986`, while the actual partition separates them.
- **Questions for the human rationale:**
  1. What unresolved issue prevents deciding how the omitted site pairs should be represented?
  2. What expertise or evidence is needed to determine whether the two morphology pairs belong together?

Human rationale:
An escalation is required because the simultaneous omission of the primary site (C12404) and associated site (C12321) 
creates topological ambiguity; structural rules alone cannot resolve which exact anatomical relationship—or 
combination thereof—is required to definitively represent the "Ovarian" origin. Furthermore, gynecologic pathology 
expertise is necessary to resolve the morphology grouping. Because "non-dysgerminomatous" is a complex, 
exclusion-based classification that often involves mixed tumor components, an SME must determine whether the two 
morphology codes (C121619 and C39986) must be grouped to form a single, indivisible composite pathological profile, 
or if they are validly asserted as separate histological features.


---

## 16. C100054 — Conjunctival Melanocytic Intraepithelial Lesion

- **Selected outcome:** Require source-reproducible correction
- **Review type:** pair-only
- **Diagnosis:** agrees-on-common-pairs
- **Expected vs actual:** The common-pair partition agrees, so there is no grouping discrepancy to resolve. The actual pairs omit expected `op:ClinicalFinding C36027` and `op:ClinicalFinding C8326`; there are no extra pairs. In the workbook, Pair Decision must equal Decision.
- **Questions for the human rationale:**
  1. Why are both omitted clinical-finding pairs material to this lesion?
  2. What source-reproducible pair-level change is required, without implying a grouping change?

Human rationale:
A source-reproducible correction is required because omitting the clinical finding pairs (C36027 and C8326) strips the 
concept of its defining pathological characteristics—specifically, its melanocytic nature and intraepithelial 
confinement. Without these clinical modifiers, the disease loses its specific diagnostic identity and is degraded to a 
generic conjunctival disorder. Because the existing grouping logic is already correct (agrees on common pairs), the 
required correction is strictly a pair-level addition: the two omitted clinical findings must be restored to the 
concept's definition without altering the current partition structure.


---

## 17. C198031 — Childhood Acute Lymphoblastic Leukemia Toronto Guidelines v2, Tier 1

- **Selected outcome:** Abstain / escalate
- **Review type:** pair-only
- **Diagnosis:** agrees-on-common-pairs
- **Expected vs actual:** The common-pair partition agrees, so there is no grouping discrepancy to resolve. The actual pairs omit `op:AssociatedRegion C12746`, `op:NormalTissueOrigin C13049`, and `op:PrimarySite C12431`, and add `op:Morphology C4005` and `op:StageValue C198022`. In the workbook, Pair Decision must equal Decision.
- **Questions for the human rationale:**
  1. What uncertainty prevents resolving the three omissions and two additions at the pair level?
  2. What source or specialist review is needed to determine the correct pair set?

Human rationale:
An escalation is required because the simultaneous removal of multiple anatomical/tissue origins 
(primary site, region, normal tissue) and the addition of morphology and stage values create profound semantic 
uncertainty for this systemic disease. Structural review alone cannot determine whether solid-tumor-style anatomical 
pairs are strictly applicable to this specific leukemia concept, nor whether "Tier 1" under the Toronto Guidelines v2 
functions strictly as an op:StageValue rather than a distinct risk or prognostic category. Pediatric hematology-oncology 
expertise and a direct review of the Toronto Guidelines are necessary to validate the morphological additions and 
confirm the exact pair set required to define this specific classification.


---

## 18. C35756 — Stage IIIB Lung Small Cell Carcinoma with Pleural Effusion AJCC v7

- **Selected outcome:** Abstain / escalate
- **Review type:** pair-only
- **Diagnosis:** agrees-on-common-pairs
- **Expected vs actual:** The common-pair partition agrees, so there is no grouping discrepancy to resolve. The actual pairs omit `op:AssociatedLineageClassification C12704`, `op:AssociatedLineageClassification C12705`, clinical findings `C155863`, `C207031`, `C3331`, `C36129`, `C36184`, `C43574`, `C54209`, `C55817`, and `C60308`, `op:StageSystem C141685`, and `op:StageValue C27978` and `C28064`; the actual pairs add `op:Morphology C4878` and `op:Morphology C9049`. In the workbook, Pair Decision must equal Decision.
- **Questions for the human rationale:**
  1. Which unresolved source or semantic questions prevent adjudicating this large pair delta?
  2. What specialist review is needed for the lineage, clinical-finding, morphology, and dual-staging differences?

Human rationale:
An escalation is required because the massive pair delta represents a wholesale remodeling of the concept's pathology 
and staging that structural review cannot adjudicate. It is semantically uncertain whether substituting multiple 
lineage and clinical finding attributes for two new morphology pairs accurately preserves the precise neuroendocrine 
identity of small cell lung cancer. Thoracic oncology and pathology expertise is needed to validate this histological 
shift, and a staging SME must evaluate the omitted stage system and values to ensure the complex clinical interaction 
between "Stage IIIB," "AJCC v7," and the prognostic "Pleural Effusion" modifier is correctly captured without semantic 
loss.
