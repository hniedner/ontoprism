# Cervical stage system/value grouping (#355)

Status: **owner/SME approved 2026-09-25**, implemented for the exact pairs below. Researched
2026-09-25 by read-only ontology-analyst session `ses_f26b50a20ffe7o6tlAV7jlICz2`.
The owner approved the exact proposal and supersession below in the #355 working
session. The active stage policy changes; the oracle, source axioms and completed
published artifact remain unchanged. No DL-reasoner
check was performed; this is not an evidenced/accepted assertion or NCI approval.

## Exact question and proposed representation

| NCIt concept | StageSystem | StageValue |
| --- | --- | --- |
| C181564 — Stage I Cervical Cancer AJCC v9 | C180901 | C27966 |
| C186620 — Stage I Cervical Cancer FIGO 2009 | C186618 | C27966 |
| C162226 — Stage I Cervical Cancer FIGO 2018 | C186617 | C96244 |

**Approved decision:** retain the two distinct axes, but put each row's exact pair in one
concept-local normalized relationship group: *this value under this staging
system/edition*. Grouping neither fuses the fillers nor equates stages across
editions. It does not add a patient-level assessment event to the ontology.

This is the parsimonious representation if normalized groups express attributes
that must be considered together: it uses the existing group mechanism to retain
the dependency without a new axis, filler, event entity or persistence format.
Both axes remain independently queryable. No global stage-grouping rule is proposed.

## Conflicting human records and NCIt source structure

The SME oracle groups these pairs in `neoplasm-adjudicated.json` (under
`ontolib/tests/decomposition/golden/`); general review metadata dates to 2026-08-03.
The later exact grouping review in
[`group-review-rationale-26.07d.md`](../../evidence/group-review-rationale-26.07d.md),
sections 1–3, approves separate groups (R. Hannes Niedner, M.D., 2026-08-28).
D87 and the packaged `normalized-group-policy.json` preserve those singleton
approvals. Neither chronology nor a higher agreement score resolves authority.

The frozen NCIt 26.07d packet records the system as inherited R88 at depth 1 and
the value as direct R88 at depth 0, in different source structural groups:

| Concept | System anchor | Value anchor |
| --- | --- | --- |
| C181564 | C181562 | C181564 |
| C186620 | C186619 | C186620 |
| C162226 | C162225 | C162226 |

See `evidence/group-review-packet-26.07d-schema3.json`, respective actual partition
citations. Those are extracted source facts, not a verbatim OWL quotation. D87
separates source groups from normalized groups: joining the normalized pair would
not claim that NCIt already groups the two source restrictions.

## Peer-reviewed evidence read

1. **Grigsby et al. (2020)**, *FIGO 2018 staging criteria for cervical cancer:
   Impact on stage migration and survival*, Gynecologic Oncology.
   [DOI 10.1016/j.ygyno.2020.03.027](https://doi.org/10.1016/j.ygyno.2020.03.027),
   [PMID 32248993](https://pubmed.ncbi.nlm.nih.gov/32248993/). Abstract read via
   NCBI E-utilities. Reclassification of the same 1,282 patients produced stage
   migration in **676/1,282 (53%)**. Stage I membership changed from 593 under
   FIGO 2009 to 354 under FIGO 2018. This supports edition-conditioned meaning;
   the unqualified label “Stage I” does not establish equivalent populations.
2. **Bhatla et al. (2019)**, *Revised FIGO staging for carcinoma of the cervix
   uteri*, International Journal of Gynecology & Obstetrics.
   [DOI 10.1002/ijgo.12749](https://doi.org/10.1002/ijgo.12749),
   [PMID 30656645](https://pubmed.ncbi.nlm.nih.gov/30656645/). Abstract read via
   NCBI and Crossref. FIGO committee revision permits imaging/pathology alongside
   clinical assessment, changes IB subdivisions, and requires recording imaging
   or pathology derivation with `r`/`p` notation. It supports retaining the
   framework and derivation context, not a particular ontology group identifier.
3. **Matsuo et al. (2019)**, *Validation of the 2018 FIGO cervical cancer staging
   system*, Gynecologic Oncology.
   [DOI 10.1016/j.ygyno.2018.10.026](https://doi.org/10.1016/j.ygyno.2018.10.026),
   [full text PMC7528458](https://pmc.ncbi.nlm.nih.gov/articles/PMC7528458/).
   Full text read. Revised IB subdivisions distinguish characteristics and
   survival outcomes. Similar top-level wording does not make editions equivalent.
4. **Bhatla et al. (2021)**, *Implications of the revised cervical cancer FIGO
   staging system*, Indian Journal of Medical Research.
   [DOI 10.4103/ijmr.IJMR_4225_20](https://doi.org/10.4103/ijmr.IJMR_4225_20),
   [full text PMC9131753](https://pmc.ncbi.nlm.nih.gov/articles/PMC9131753/).
   Full text read. Contrasts clinical staging through 2009 with the 2018 option
   to add imaging and pathology, and describes resulting stage shifts.
5. **Olawaiye et al. (2021)**, *The new (Version 9) American Joint Committee on
   Cancer tumor, node, metastasis staging for cervical cancer*, CA: A Cancer
   Journal for Clinicians.
   [DOI 10.3322/caac.21663](https://doi.org/10.3322/caac.21663),
   [PMID 33784415](https://pubmed.ncbi.nlm.nih.gov/33784415/). Abstract read via
   NCBI and Crossref; publisher full text returned 403. Describes incorporation
   of imaging/surgical findings, removal of lateral spread from T1a and addition
   of T1b3. Its statement that changes “align with” FIGO does not assert equivalence.

Pecorelli et al. (2009), DOI `10.1016/j.ijgo.2009.02.009`, PMID `19342051`, was
identified in NCBI, but no abstract/full text was available in this investigation.
It is not used for substantive claims beyond identifying the 2009 revision.

## Curated ontology and interoperability evidence read

- **NCI EVS REST**, concept, parent and role endpoints for the three concepts:
  [C181564](https://api-evsrest.nci.nih.gov/api/v1/concept/ncit/C181564),
  [C186620](https://api-evsrest.nci.nih.gov/api/v1/concept/ncit/C186620),
  [C162226](https://api-evsrest.nci.nih.gov/api/v1/concept/ncit/C162226).
  Returned **26.08e**, including when 26.07d was requested; these current
  definitions corroborate meaning but do not replace pinned 26.07d evidence.
  Definitions explicitly qualify Stage I by AJCC 9th edition, FIGO 2009 or FIGO
  2018; parent definitions likewise identify the edition. Both system and value
  appear as Disease_Is_Stage roles. Flattened REST roles do not establish OWL
  grouping. The FIGO top-level descriptions are similar; that does not negate
  the clinical edition differences documented above.
- **HL7 mCODE 4.0.0 STU4**, active 2025-02-16,
  [Cancer Stage profile](https://hl7.org/fhir/us/mcode/STU4/StructureDefinition-mcode-cancer-stage.html)
  and [FIGO example](https://hl7.org/fhir/us/mcode/STU4/Observation-figo-stage-IIIA.html).
  The profile calls stage an assessment “according to a given cancer staging
  classification system.” Separate `method` and `value[x]` fields belong to
  the same Observation. This supports distinct axes **with association**, not
  a requirement for separate ONTOPRISM normalized groups.
- **SNOMED International public glossary**,
  [attribute group](https://docs.snomed.org/snomed-international-documents/snomed-ct-glossary/a/attribute/attribute-group.md):
  “An association between a set of attribute value pairs that causes them to be
  considered together within a concept definition or postcoordinated expression.”
  This is a general modeling principle, not inspection of SNOMED's exact cervical
  cancer concepts. No licensed release was accessed.

## Inference, limitations and approval needed

Clinical literature establishes that stage meaning depends on its framework.
NCIt preserves both constituents; mCODE associates distinct method/result fields;
SNOMED explains why grouped attributes are considered together. **No source
directly mandates an ONTOPRISM normalized group.** The proposed grouping is an
explicit modeling inference from those sources, not a quoted scientific rule.

With exactly one system and one value, concept-level co-occurrence can already
preserve their association for some consumers. A shared normalized group makes
that association explicit without sacrificing orthogonal queries. The prior
rationale conflates separating fields with separating normalized groups; mCODE
requires neither loss of the association nor ONTOPRISM singleton groups.

The approval supersedes only the three 2026-08-28 singleton decisions and D87's
corresponding clause, preserves the existing oracle, and authorizes changing only the exact
normalized policy pairs. Official source axioms remain unchanged. Output stays
provisional enhanced NCIt, derived from NCIt, aligned to the modeling patterns and
corroborated by the cited literature. No filler or cross-edition equivalence,
scientific acceptance, new persistence or completed-artifact republication follows.
