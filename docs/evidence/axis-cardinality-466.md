# Axis cardinality (#466, owner approval 2026-10-06)

Cardinality is a rule over the enhanced-NCIt projection, not an assertion that
an NCIt class is equivalent to a clinical record or a SNOMED relationship group.

Approved: ClinicalFinding and MetastaticSite 0..*. PrimarySite stays 0..1 under D58.
Additional tier-0 rule: an axis on which NCIt itself states multiple non-nested
values is 0..*, unless a curated class-level source establishes 0..1. Stage and
Grade remain unresolved because mCODE's limits apply per assessment.

Current source census (NCIt26.07d, Neoplasm C3262 including root, 15634 concepts):
own stated existential restrictions only, named fillers; nested intersection members
included; inherited restrictions excluded. Denominator: concepts with an own value
on that source-role axis. Remove only strictly broader fillers using the production
`read_scope_hierarchy_edges` named subclass + definition-genus traversal (247846
distinct edges); reverse reachability prevents cyclic candidates eliminating each
other. These are absence-of-told-nesting counts, not proof of semantic disjointness.

| Axis | Non-nested multiple / concepts with values | Share | Cardinality |
|---|---:|---:|---|
| PrimarySite (R101 before semantic routing) |75/1440|5.208%|0..1, D58 overrides|
| MetastaticSite |1/134|0.746%|0..*|
| AssociatedSite |6/178|3.371%|0..*|
| NormalTissueOrigin |4/367|1.090%|0..*|
| CellOrigin |2/376|0.532%|0..*|
| CellType |140/994|14.085%|0..*|
| MolecularAbnormality |36/296|12.162%|0..*|
| CytogeneticAbnormality |6/154|3.896%|0..*|
| ClinicalFinding |693/2391|28.984%|0..*|
| StageValue |12/1736|0.691%|unresolved|
| StageSystem |5/181|2.762%|unresolved|
| Grade |0/142|0%|unresolved|

Derived R101 routes (PrimarySubsite, AssociatedRegion, AssociatedLineageClassification)
are not separate source-role measurements; their existing approved routing handling
is retained. Genus morphology and label-derived axes have no direct source-role
denominator in this census and gain no new limit here. R88 is split by the existing
approved stage-system code classification. The earlier historical 11.3% molecular
share is not substituted for the measured 12.162%.

Curated comparisons: SNOMED CT US2026-03-01 MRCM Finding site363698007 permits0..*
overall,0..1 per group; Associated with47429007 permits0..* both. Neither is a
class-wide 0..1 override for the additional axes. HL7 mCODE4.0.0 STU4
[Primary Cancer Condition](https://hl7.org/fhir/us/mcode/STU4/StructureDefinition-mcode-primary-cancer-condition.html)
permits repeated bodySite/evidence.code;
[Secondary Cancer Condition](https://hl7.org/fhir/us/mcode/STU4/StructureDefinition-mcode-secondary-cancer-condition.html)
permits repeated metastatic locations.
[Cancer Stage](https://hl7.org/fhir/us/mcode/STU4/StructureDefinition-mcode-cancer-stage.html)
has value/method0..1 per assessment;
[Histologic Grade](https://hl7.org/fhir/us/mcode/STU4/StructureDefinition-mcode-histologic-grade.html)
has value1..1 per assessment. Do not transfer these limits to a whole NCIt class.

For approved0..* axes, multiple known routed values are not an ambiguity or review
reason. Unknown routing stays review-bearing. A sourced0..1 excess is a limit
violation; unresolved class-level grouping/nesting remains explicitly undecidable.
No new persistence or equivalence assertion is introduced.
