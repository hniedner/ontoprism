# Repository release changes and enhancement carry-forward (#490)

**Status: measurement record and design in progress; not owner-approved.**
No source activation or automatic carry-forward is authorized by this document.
The design must be approved before #491 and the implementation adjustments to
#492–#494. Existing source stores, historical curator decisions and completed run
artifacts remain unchanged.

## Measured release pair

Compare certified **stated** NCIt 26.07d and 26.09d OWL, not inferred closure.
This spans two monthly updates, including **26.08e** (not 26.08d). The publisher's
[August README](https://evs.nci.nih.gov/ftp1/NCI_Thesaurus/archive/26.08e_Release/ReadMe.txt)
identifies August's fifth build, editing completed August 31, 2026.
The [current README](https://evs.nci.nih.gov/ftp1/NCI_Thesaurus/ReadMe.txt), inspected
October 10, identifies 26.09d, editing completed September 28. Stated OWL retains
retired concepts; the inferred distribution excludes them. Removal from a hierarchy
or inferred file must therefore not be equated with removal from stated OWL.

Inputs:

| Release | Local stated input | Bytes |
|---|---|---:|
|26.07d|`data/ncit-owl/Thesaurus-stated.owl`|749,451,268|
|26.09d|`tmp/issue-490-ncit-26.09d/Thesaurus-stated.owl`|753,577,794|

The 26.09d pair was downloaded and certified using existing `data-build owl` with
`NCIT_OWL_DIR=tmp/issue-490-ncit-26.09d`. It has not been loaded or activated.
The active source remains 26.07d. A comparison-store preparation step is still
needed for #491's two-certified-store interface.

## ROBOT evaluation: observed capabilities and limits

The owner ran pinned ROBOT **1.9.10** locally. Times and maximum RSS below are
owner measurements; the agent inspected the resulting reports, not those processes.
No application dependency, launcher, parser setting or container flag was changed.

| Attempt | Result | Wall time | Maximum RSS |
|---|---|---:|---:|
|Small axiom fixture, existing launcher|Exit 0; expected existential replacement and new class/parent|0.94 s|145,801,216 bytes|
|Full pair, existing launcher (`-Xmx4g`)|Exit 1, `JAXP00010006`: depth 101 exceeds `jdk.xml.maxElementDepth=100`, left file line 5090|0.86 s|Not measured/reported|
|Full pair, launcher plus `JDK_JAVA_OPTIONS=-Djdk.xml.maxElementDepth=0`|`OutOfMemoryError: Java heap space`, still parsing left input|44.1 s|4.79 GB (owner-reported units)|
|Full pair, same jar, `-Xmx24g -Djdk.xml.maxElementDepth=0`|Exit 0|55.72 s|17,120,133,120 bytes (15.94 GiB)|

Successful full-pair command, from the repository root:

```bash
/usr/bin/time -l pdm run java -Xmx24g -Djdk.xml.maxElementDepth=0 -jar .tools/robot-1.9.10/robot.jar diff --left data/ncit-owl/Thesaurus-stated.owl --right tmp/issue-490-ncit-26.09d/Thesaurus-stated.owl --format plain --output tmp/plans/issue-490-robot-full-26.07d-26.09d.txt
```

**Tooling finding, not a launcher change:** the current 4 GiB launcher cannot run
this full comparison. The owner's operating requirement for repository use of
ROBOT on full NCIt is both a heap of **at least 20 GiB** and
`-Djdk.xml.maxElementDepth=0`; the successful tested configuration is **24 GiB**.
The exact minimum sufficient heap was not measured. Disabling the XML depth cap
removes a parser resource limit: restrict this execution profile to the inspected,
certified publisher inputs, not arbitrary uploaded XML. This is a scoped future
tool-execution requirement, not permission to weaken runtime/CI defaults globally.
Keep the launcher unchanged during #490 as the owner directed.

The original 5–20 minute / 3–6 GiB RSS estimate was wrong: the successful invocation
was faster and used substantially more memory. Tiny-fixture memory did not predict
full OWLAPI parsing requirements. Heap size is not process RSS. A single successful
run establishes feasibility, not repeatability or an upper bound.

The completed report is **6,340,567 bytes, 25,162 lines**; retain it, never overwrite
it for a rerun. Agent read-only census reconciles every entry:

| OWL construct | Only in 26.07d | Only in 26.09d |
|---|---:|---:|
|AnnotationAssertion|2,949|18,271|
|SubClassOf|373|1,849|
|EquivalentClasses|217|441|
|Declaration|1|1,050|
|Ontology Annotation|2|2|
|AnnotationPropertyRange|1|1|
|DatatypeDefinition|1|1|
|**Total**|**3,544**|**21,615**|

Logical change rows have 556 distinct named first-argument classes on the removed
side and 1,849 on the added side, 1,874 in their union. These are whole-release
report subjects, **not yet Neoplasm changed-definition counts**: they include new
classes and OWL equivalence expressions whose members require structural handling.
ROBOT supplies axiom-level additions/removals and ignores serialization differences;
it does not establish the meaning of NCI merge/split events or independently prove
that an overlay assertion remains supported. Do not replace structural expression
parsing with regex extraction of filler codes from nested axioms.

## Publisher history: documentation versus actual data

The authoritative [history README](https://evs.nci.nih.gov/ftp1/NCI_Thesaurus/ReadMe_history.txt)
documents `conceptcode|editaction|editdate|referencecode` and the actions create,
modify, split, merge and retire. A split retains the original code and creates a
new code; merge records include a self-reference for the surviving code. Retirement
can have one row per old superconcept. Count events/concepts, not raw history rows.

Actual `monthly_history_26.08e.txt` and `monthly_history_26.09d.txt` have **six**
pipe-separated fields, e.g. `C123981||merge|31-Aug-26|C123981|`. Every inspected row
has empty second and sixth fields; observed retirement samples use literal `null`.
Any parser must explicitly validate this observed layout, rather than silently
discarding arbitrary extra fields to fit the documentation.

| File | Bytes | Rows | create | modify | merge | retire | split |
|---|---:|---:|---:|---:|---:|---:|---:|
|26.08e monthly history|78,258|2,464|608|1,823|18|9|6|
|26.09d monthly history|960,580|30,553|442|30,066|30|15|0|

These are whole-file action **row counts**, not a Neoplasm census. A `modify`
event need not change a stated logical definition. The August split rows concern
three original codes. The scope join below distinguishes these lifecycle events
from source-definition changes and overlapping published impact cohorts.

## Neoplasm impact census (October 10, 2026)

Read-only command: `pdm run python tmp/scratch/issue490_impact.py`. This joins the
completed ROBOT report and publisher monthly history to source hierarchy inventories
and the published v8 run `neoplasm-44721971-4466-4e2a-8952-4ba06edc0025`.
The public marker was checked before counting its flags. No store was activated.

The diagnostic streams top-level named classes from each certified stated RDF/XML
file, reading direct named subclass edges and named members of equivalent-class
intersections. All observed genus lists use RDF Collection syntax (23,940 / 24,164).
It reuses production `scope.descendant_codes` for strict C3262 descendants. The
26.07d edge set and scope exactly match production `read_scope_hierarchy_edges`;
the 26.09d scope is file-derived, not yet verified against a served sibling store.
This is a scope/impact adapter, not a second OWL axiom differ: logical changes come
from ROBOT's SubClassOf and EquivalentClasses rows. It does not flatten restrictions.

| Inventory | 26.07d | 26.09d |
|---|---:|---:|
|Named NCIt classes in stated file|212,475|213,524|
|Named hierarchy edges|247,846|249,474|
|Strict Neoplasm descendants|15,633|15,808|

Whole release: **1,049 new named classes**, no removed named classes, **24 retired
codes**, all 24 having merge-away history; three split-original codes, none in the
union of the two Neoplasm scopes. The 1,050 added Declaration axioms are therefore
not interchangeable with the net-new named-class count.

Neoplasm: 15,628 concepts remain in both scopes; **180 new concepts enter**, with
no pre-existing code entering; **five concepts exit**, all retired through mergers.
All five remain declared in stated OWL. **203 retained in-scope concepts** have
changed logical definitions, or **208 old-scope concepts including retirees**.
Across the union scope ROBOT reports 205 removed / 383 added EquivalentClasses
axioms and four removed / five added SubClassOf axioms, including new concepts.

### Published outcomes and attached flags touched

The existing run has 15,633 outcomes, of which 14,884 are decomposed. Its served
graph has 11,375 review flags on 6,233 concepts. For each change set, count subjects
by three joins: (a) subject itself changed, (b) a stored definition fact's
`anchor_code` changed, or (c) an emitted `filler_code` changed. Deduplicate subjects
before counting their published flags. These are **re-examination candidates**, not
assertions that every inherited fact or attached flag became invalid. This census
does not yet cover all hierarchy paths, range witnesses, P334 annotation changes,
or dependencies on facts absent from the stored projection.

|Change set|Changed codes|Direct outcomes / decomposed|Union outcomes / decomposed|Union flagged concepts|Attached flags on union|
|---|---:|---:|---:|---:|---:|
|New Neoplasm concepts|180|0 / 0|0 / 0|0|0|
|Retired/merged-away Neoplasm concepts|5|5 / 4|79 / 78|17|38|
|Changed definitions, still in Neoplasm|203|203 / 200|1,200 / 1,197|530|941|
|Changed old-scope definitions including retirees|208|208 / 204|1,205 / 1,201|530|941|
|All retained-class logical changes, including outside Neoplasm|825|208 / 204|1,235 / 1,231|560|986|

New concepts have no old published outcome or reference in these joins; they need
new derivation, not zero-work carry-forward. The last row shows why source changes
outside the branch matter: another 30 published decompositions and 45 attached
flags enter the candidate cohort through changed external-to-scope fillers.
Rows overlap and must not be summed.

For the 208 changed old-scope definitions, stored source-anchor joins touch 1,199
decompositions carrying 940 flags; emitted-filler joins touch 803 decompositions
carrying 685 flags. Direct changes touch 204 decompositions and four other outcomes,
carrying 109 flags. Their union is the 1,205 outcomes / 941 flags above.
There are 106 changed emitted filler codes, used in 1,006 constituent emissions;
10 of those emissions have `needs_review`. Across all 825 logical-change subjects,
116 changed fillers occur in 1,038 emissions, 30 flagged. These emission-level
flags are distinct from all flags attached to the affected concept cohort.

### Retirements individually

|Old code|Publisher merge survivor|Touched outcomes / decomposed|Flagged concepts|Attached flags|
|---|---|---:|---:|---:|
|C173176|C173118|15 / 15|1|2|
|C173400|C173408|4 / 4|0|0|
|C202625|C164198|66 / 66|12|27|
|C6642|C6606|12 / 12|5|11|
|C67453|C228940|1 / 0|0|0|

The first four mergers are dated August 31; C67453 is September 28. P310 becomes
Retired_Concept on each; C67453 was already Obsolete_Concept in 26.07d. Four retired
codes occur as fillers in 19 old emissions (none flagged); C67453 has no emitted
filler or stored source-anchor reference. Shared dependants explain why individual
rows sum to more than the deduplicated retirement union of 79 outcomes / 38 flags.
Publisher merge targets do not themselves authorize semantic rebinding.

AnnotationAssertion impact is **not yet measured**: ROBOT also emits nested axiom
annotations, which a simple first-subject regex does not fully handle. Preliminary
annotation-only counts were discarded. This limitation must be resolved for complete
P334/provenance carry-forward; it does not alter the logical-definition counts above.

## Remaining design evidence and decisions

- Complete assertion-specific support impact, including annotation changes and
  hierarchy/range dependencies beyond the measured stored-reference joins above.
- Execute OAK/KGCL on samples and the real pair, measuring wall time/RSS and checking
  whether its output preserves nested restrictions, named parents and annotations.
  OAK is not installed in the application environment; capability/performance is
  **not verified**. No recommendation to replace ROBOT is established yet.
- Build the carry-forward table for each overlay kind and source change. Keep
  publisher lifecycle evidence distinct from a semantic decision to rebind a code;
  do not choose split successors heuristically or infer adoption from labels.
- Determine ledger requirements (#347) and repository-specific refresh sequences,
  using existing source certification/activation and provenance mechanisms rather
  than parallel implementations. New persistence is not approved by this draft.
- Obtain the owner's design approval and then adjust #492–#494 accordingly before
  implementing the read-only release-delta command in #491.
