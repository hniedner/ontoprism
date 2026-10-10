# Repository release changes and enhancement carry-forward (#490)

**Status: owner-approved design**, subject to the completion conditions in
[#490 comment 6101202146](https://github.com/hniedner/ontoprism/issues/490#issuecomment-6101202146).
No source activation or automatic carry-forward is authorized by this document.
Proposed #491–#494 and #347 contract edits require separate owner approval.
Existing source stores, historical decisions and completed artifacts remain unchanged.

## Simplest path first: full re-derivation, not an incremental engine

Owner direction (October 10, 2026): a full Neoplasm run takes about **33 minutes**
(the v8 publication took 1,968.20 seconds). **Always fully re-derive engine output
on the candidate release**, including outcomes, constituents, generated flags and
machine-derived evidence. The change census explains the release and informs human
review; it must not select a subset of concepts for engine execution. #491–#494
must not introduce incremental re-derivation machinery or an engine dependency
invalidation service on the strength of this design.

Dependency-closure impact tracking is needed **only for human decisions**: curator
decisions, human flag dispositions and human-reviewed evidence. Per the owner,
none of this release-overlay human curation exists before G1. Before G1, report
that inventory as empty and re-derive the entire candidate corpus; do not build
incremental machinery for a future inventory. Historical development/oracle
decisions remain retained records, not evidence that G1 curation has occurred.
When human decisions exist, check their full support before carrying applicability
forward; unchanged codes or unchanged emitted pairs alone are insufficient.

**Pin the comparison engine.** Candidate-release re-derivation uses the engine
version of the baseline run it is compared with, including its versioned rules,
policy inputs and run configuration. Only the source release changes in that
before/after. An engine upgrade is a separate, explicitly versioned run; do not
compare an upgraded engine on a new release against the old baseline and attribute
the combined difference to source change. If the baseline engine/configuration
cannot be reproduced on the candidate, report the blocked comparison rather than
silently substituting the current engine. The measured 33 minutes is a planning
reference, not a candidate-release runtime guarantee.

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

The 5–20 minute / 3–6 GiB estimate was wrong: faster, but more memory. Heap is not
RSS; one success establishes feasibility, not repeatability or an upper bound.

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

Logical rows name 556 removed-side / 1,849 added-side classes, union 1,874; these
whole-release subjects include new classes, not just changed Neoplasm definitions.
ROBOT axioms do not supply lifecycle meaning or prove overlay support. Nested
expressions require structural parsing, not regex filler extraction.

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

The diagnostic streams direct named subclass and equivalent-intersection genus
edges from stated RDF/XML (23,940 / 24,164 RDF Collections), reusing production
`scope.descendant_codes`. The 26.07d edges exactly match the production reader;
26.09d is file-derived, not yet checked against a served sibling. Logical changes
come from ROBOT, not a second axiom differ; restrictions are not flattened.

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

The run has 15,633 outcomes / 14,884 decompositions and 11,375 flags on 6,233 concepts.
Join changed subject, stored fact `anchor_code`, or emitted `filler_code`; deduplicate
subjects before counting flags. These are **re-examination candidates**, not invalid
assertions. Unrecorded dependencies, hierarchy/range paths and P334 annotation
changes are not exhaustively covered by these joins.

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

AnnotationAssertion impact is **not measured**: nested axiom annotations defeated
simple subject extraction, so preliminary counts were discarded. Future human
P334/provenance carry-forward must resolve this; logical counts above are unaffected.

## OAK/KGCL evaluation: full-pair timeout and tooling decision

Estimate posted first: 10–30 minutes, 8–24 GiB RSS, 60-minute timeout. Isolated
`tmp/issue-490-oak-venv`: OAK 0.7.4, Pronto 2.7.3, fastobo 0.14.1, kgcl-schema
0.7.0, kgcl-rdflib 0.5.0, RDFLib 7.6.0, Python 3.14; HOME/PYSTOW_HOME under tmp/.
No project dependency or launcher changes. `runoak diff --help` recommends ROBOT
for complete OWL diffs. Sample selector failures (`rdf:`, endpoint-style `rdflib:`,
Turtle adapter without DifferInterface) did not test full NCIt.
The Pronto RDF/XML sample took 2.52 s / 224,395,264 bytes RSS: one ClassCreation,
NodeRename, EdgeDeletion and NodeMove each. It omitted the new class's parent edge;
NodeMove represented an added restriction edge, not semantic code migration.

|Question|ROBOT evidence|OAK/KGCL evidence so far|
|---|---|---|
|Complete axiom additions/removals|3,544 / 21,615 on actual pair|Timed out; incomplete 645 ClassCreation events cannot be reconciled as a full axiom diff|
|Label rename|AnnotationAssertion removal/addition|Explicit NodeRename observed on sample|
|New named class and parent|Separate declaration and subclass axioms|ClassCreation observed; new class parent edge omitted on sample|
|Existential replacement|Removed/added ObjectSomeValuesFrom axioms|EdgeDeletion + NodeMove observed; not full expression preservation evidence|
|Retirement / merge|Publisher history supplies lifecycle meaning|KGCL has event types, but recognition of NCIt P310/history is unverified|

After the agent's launch was denied, the owner launched attempt 1 at
**2026-10-10T17:30:04Z**, PID 4365, with the following timed command:

```bash
HOME="$PWD/tmp/issue-490-oak-home" PYSTOW_HOME="$PWD/tmp/issue-490-oak-cache" /usr/bin/time -l gtimeout --kill-after=30s 3600s tmp/issue-490-oak-venv/bin/runoak -i pronto:data/ncit-owl/Thesaurus-stated.owl diff -X pronto:tmp/issue-490-ncit-26.09d/Thesaurus-stated.owl -O json
```

**Finding:** OAK/Pronto did not complete this pair within **60 minutes on this
machine**. Retained owner-run evidence: exit **124**, **3,600.17 s real**, 3,582.66 s
user, 12.68 s sys, **8,458,715,136 bytes RSS**, two signals. Agent inspected the
files; owner confirmed process exit. Partial JSON: **123,194 bytes / 645
ClassCreation tokens**, incomplete array; all output by 17:31Z, then 59 minutes
without output. Warning: `cannot process plain owl:AnnotationProperty`. No full
rename/obsoletion/merge coverage or axiom-count parity was demonstrated.
Owner sampling showed entity-generator set construction before the relationship
pool; Python line-level cause is **not verified**. This does not prove an infinite
loop or failure of every adapter. **No retry or OAK repair**, per owner; retain
`tmp/issue-490-oak-runs/full-attempt-1.*` unchanged, never repair partial JSON.
**Decision:** ROBOT with measured JVM settings plus NCI monthly history; #491
derives display events from these, **no OAK dependency**. Label assertion pairs
can explain renames; publisher history explains lifecycle, not identity. The OAK
runtime estimate was exceeded; 60 minutes is a failure bound, not a successful time.

## Approved carry-forward rules

The overlay principle is proposed, not an authorization to implement new storage:
official sources remain immutable. Separate the historical curator decision from
its applicability to a candidate release. Unchanged codes alone do not prove
unchanged support. Incomplete or conflicting dependency information goes to review.

|Overlay item / source change|Proposed outcome|Required check or review reason|
|---|---|---|
|Engine output, with any source change or none|Fully re-derive the entire candidate Neoplasm scope with the baseline engine version|Never use dependency impact to select incremental engine work; engine upgrades are separate runs|
|Curator decision, all supporting assertions and applicable policy unchanged|Carry decision unchanged, scoped to candidate release|Check structured assertion/group context, source semantics and policy applicability|
|Supporting restriction, parent, group structure, range witness or provenance changes|Re-derive engine assertion; return affected human decision to needs-SME|Name changed support and retain old rationale; do not retain an approval on a different assertion|
|Label-only change on a stable code|Carry nonlexical decision if its support is unchanged; refresh display label|Re-derive lexical/NLP-dependent assertions instead of assuming label independence|
|New concept or existing concept enters scope|Derive new outcome and evidence|No old decomposition to carry; do not infer acceptance from an adjacent concept|
|Concept exits scope but remains active|Keep historical decision; mark inapplicable to this scope|Explicit scope-exit reason, no silent deletion|
|Concept retires without a unique supported successor|Keep historical item, queue unresolved successor|Retired code is not a current assertion endpoint|
|Publisher merge with one surviving code|Propose rebinding; re-derive and check support; needs-SME unless equivalence of this assertion's support is established|Merge history identifies survivor but does not establish interchangeability of every old assertion|
|Publisher split|Re-derive candidates; needs-SME for target selection|Original code survives; neither it nor a new branch inherits approval by default|
|P334 anchor annotation or any relevant told path changes|Recompute all minima, absence and ambiguity; re-examine affected decisions|Anchor membership and uniqueness depend on the candidate set, not only one retained path|
|Per-assertion evidence / external range evidence|Recheck source assertion and applicability; retain citation history|Evidence tier and source version remain explicit; provenance is not new evidence|
|Generated review flag, cause disappears or changes|Recompute in the full run; separately reassess any human disposition|Do not copy old flags wholesale or treat disappearance as expert acceptance|
|Proposal appears to be adopted upstream|Recognize adopted status only with verified structural release evidence|Labels or matching codes alone are insufficient; conflicting evidence goes to needs-SME|
|Mapping endpoint or domain/range changes|Revalidate mapping; queue unresolved cases|No promotion to identity grade or automatic equivalence from release history|
|caDSR CDE version/workflow changes|Compare the versioned CDE and relevant semantic fields; re-examine affected annotations|Public ID alone does not identify unchanged versioned support|
|Uberon obsolete term / replacement changes|Use publisher replacement as a candidate, revalidate each assertion|Multiple replacements or changed range entail review, not guessed rebinding|
|ICD-O edition/code meaning changes|Revalidate edition-qualified histology/behavior/topography support|Equal printed code across editions does not alone prove stable meaning|

The human carry-forward rules are conditional: the impact census identifies
candidate review cohorts, not proof that every untouched decision remains applicable.
Human support checks must consider inherited facts and newly added candidates or
paths that change minima. This dependency closure protects human decisions only;
the engine recomputes all concepts regardless of the impact set. With the pre-G1
human inventory empty, there are no such applicability decisions to carry forward.
Flag and decision histories must remain visible even when their current applicability
changes. Queue reports must give before/after counts by reason and axis, along with
the evidence-supported share; no change is silently dropped.

### Consequences for the ledger (#347), not a schema proposal

For human decisions once they exist, the logical key must identify the subject,
axis/relation, filler and the relevant
source definition/group occurrence, plus source release and decision scope. Reuse
existing structured provenance and policy records; run-local occurrence/group IDs
alone are not a cross-release semantic key. Capture enough structural support to
re-evaluate the assertion without declaring two internal artifacts identical by hash.
Evidence citations, author/rationale, lifecycle and original decision must remain
separate from candidate-release applicability and any proposed successor binding.
The exact migration/tables/file formats remain subject to separate issue approval.

### Shared refresh sequence and activation boundary

1. Prepare and certify an inactive candidate; retain the active source. Obtain
   publisher history, compare actual source facts, and reject conflicting releases.
2. Compute lifecycle, structural, annotation and scope changes for explanation.
   Inventory human decisions; evaluate dependency closure only for those decisions,
   recording unresolved support with reasons. Before G1, this inventory is empty.
3. Fully re-derive all candidate Neoplasm engine outputs using the baseline run's
   engine version, rules, policies and configuration. Recompute machine evidence
   and flags; separately recheck human-reviewed evidence and decisions, if any.
   Rebuild candidate search/export views from that same candidate. Report source
   deltas at the pinned engine; perform any engine upgrade as a separate run.
4. Report preserved/re-derived/rebound/review/adopted counts, queue and evidence
   distributions. Obtain the owner's release decision; no source activation here.
5. Activate one coherent source-plus-overlay generation. Existing NCIt journaled
   activation and caDSR replacement provide building blocks, **not yet a guarantee
   of atomic source/overlay activation across QLever and Postgres**. The approved
   implementation must define a single visible generation and crash recovery rather
   than claim a distributed transaction that does not exist.
6. Verify readiness, source/overlay agreement and representative reads; preserve
   the old generation for authorized recovery. Apply analogous candidate/certify/
   generation activation to Uberon and ICD-O without mixing publisher releases.

## Publisher-specific evidence and refresh sequences

Read-only configured-store census: `pdm run python tmp/scratch/issue490_publishers.py`.
SQLite opened with `mode=ro`; Postgres transaction read-only; QLever SELECT only.
These are installed release facts, not claims about newer publisher releases.

|Publisher supplies / configured observation|What ONTOPRISM must compute|
|---|---|
|Uberon OWL version IRI `.../releases/2026-06-19/uberon.owl`, `owl:deprecated` and `IAO:0100001` replacement assertions. Among UBERON-prefixed terms: **1,387 obsolete**, **535 with replacement**, **852 without**, **0 multiple replacements**. Imported CL terms excluded from these counts.|Compare versioned axioms/annotations and affected mappings; validate replacement targets/ranges, detect missing or conflicting successors. A replacement is not identity or automatic mapping approval.|
|caDSR versioned CDE export: configured `cdes` has composite key **public_id/version**, both TEXT; workflow_status and registration_status are separate fields. **79,835 rows / 79,157 public IDs**, zero null version/workflow values. Versions include `1`, `1.1`, `2.31`, `16` (not safely parsed as floats).|Compare version-qualified CDE semantics, permissible values and NCIt anchors. Distinguish missing-from-export from retirement; publisher status and comparison evidence decide lifecycle. Version ordering requires defined semantics, not numeric guessing.|
|ICD-O generation manifests identify **edition, axis, publisher URL, source artifact and reader**, not just a printed code. Active **3.2 morphology: 1,143**, **4.0 morphology: 2,390**, **4.0 topography: 406** records; no 3.2 topography generation. Publisher URLs identify IACR 3.2 and IARC's `/icd-o-4/` distribution.|Compare edition/axis-qualified code presence, terms, behavior and notes; revalidate NCIt P334 support against the applicable edition. No monthly lifecycle feed was established in these configured sources; do not invent merge/split events from missing codes.|

caDSR workflow counts: RELEASED **60,301**, RETIRED ARCHIVED **16,845**, DRAFT NEW
**2,595**, DRAFT MOD **93**, RELEASED-NON-CMPLNT **1**. Registration includes both
publisher spellings `Superceded` (638) and `Superseded` (112), and `Retired` (24);
do not conflate registration with workflow retirement. Compare raw statuses as well
as any explicitly approved normalization. The export is not an exhaustive history.

Each repository has a candidate phase ending in a report and owner release decision;
only explicit subsequent activation exposes the new generation, followed by a final
report. Failure before activation leaves the active generation untouched.

- **NCIt:** download/certify stated+inferred pair → existing offline sibling builder
  → ROBOT axiom delta + monthly history → full Neoplasm re-derivation at baseline
  engine version → human-only applicability checks → candidate search/export and
  smoke/readiness → report and owner release decision → journaled coherent
  source/overlay activation → readiness/recovery check and final report.
- **caDSR:** download source archive to a separate candidate path → existing
  `cadsr.build` database/FTS/provenance validation → compare public ID/version,
  workflow and anchors against active NCIt → recompute generated links and check
  any human decisions → certify dependent views/embeddings and smoke → report and
  owner release decision → reuse `scripts/data_build.py::_build_cadsr`'s validated
  candidate `.replace(destination)` path (same-filesystem SQLite replacement),
  coordinating dependent overlay generations → reopen/certify readers and final
  report. SQLite replacement alone is not atomic publication of Postgres overlays.
- **Uberon:** obtain/certify pinned OWL → build inactive index via existing
  `uberon.store` → compare axioms, obsolete/replacement annotations and affected
  mappings/ranges → recompute derived evidence, check human applicability → candidate
  search/smoke/readiness → report and owner release decision → coherent source and
  mapping-view activation with retained recovery index → verify and final report.
- **ICD-O:** obtain entitled edition-specific workbook/annexes → existing ingest
  format validation and generation certification → compare each edition/axis's
  records and P334 uses → recompute derived support, queue unresolved human items
  → candidate access-policy/search/smoke checks → report and owner release decision
  → existing active-generation publication coordinated with overlay views → verify
  edition/axis/readiness and final report. Preserve entitlement and source provenance.

## Implementation follow-through (contracts await owner approval)

- For future human decisions, resolve support checks for annotation and hierarchy/
  range dependencies beyond the measured joins; do not build incremental engine
  re-derivation. No human release-overlay decisions need migration before G1.
- Implement the owner's ROBOT-plus-history choice in #491 without adding OAK;
  handle nested axiom structures explicitly when deriving display events.
- Submit proposed #491–#494 and #347 contract edits together; do not edit issues
  without owner approval. No schema, persistence, launcher or dependency changes
  are authorized by the design approval.
