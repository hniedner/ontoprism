# Decomposition golden-set candidates and adjudicated oracle

For plain-language definitions of decomposition, axes, fillers, source occurrences, curated
projections, and relationship groups, see the [shared terminology](../../../../README.md#terminology).
This evidence record retains the exact axis names used by the source rows.

## Files in this directory

| File | What it is |
|---|---|
| `neoplasm-adjudicated.json` | The M1 `SME-ADJUDICATED` oracle. |
| `neoplasm-row-decisions.json` | The selected row-decision projection used for the SME-label baseline. |
| `neoplasm-engine-evidence.json` | The recorded engine run scored against the oracle. |
| `neoplasm-corpus-comparison.json` | The tracked residual comparison for the M1 sample. |
| `neoplasm.json`, `neoplasm-draft.json` | `AUTO-DRAFT` review inputs. **Not** oracles. Retained as seeds. |
| `proposal-registry.json` | The sole current strict governance record for proposals bound to the oracle. |
| `complete-definition.json` | Fixture for the complete-definition path. |
| `neoplasm-current-engine-evidence.json` | Current-source 20-code replay evidence; never the historical attested run. |
| `neoplasm-current-comparison.json` | Current replay metrics, grouping diagnoses, and all 189 row classifications. |
| `neoplasm-current-corpus-baseline.json` | Current-source v5 full-corpus counts and exact representation identity. |
| `neoplasm-highest-fanout.json` | Exhaustive current-source highest-fanout concepts and fixed query budgets within the 15,633-concept C3262 neoplasm scope (not all NCIt). |
| `neoplasm-r101-v5-2b39-historical-conservation.json.gz` | Immutable schema-3 source diagnostic for the historical 2b39 mixed-chain inventory/projection era. |
| `neoplasm-r101-v5-corrected-projection.json` | Immutable corrected projection bound to the historical 2b39 inventory and source diagnostic; not a run. |

`proposal-registry.json` is the sole current strict golden governance record for minted proposals.
It records `MINT-781c8c8c6096` as `locally-approved`, meaning local SME approval only—not
submission, acceptance in NCIt, runtime publication, or full-corpus publication. Runtime database
minted concepts and the API that serves them are separate product state; this directory does not
replace or certify those runtime surfaces. The C27787 adjudication rationale contains superseded
lifecycle prose whose identity-bound correction is deferred and blocked as recorded in D82; the
strict registry, not that prose, is current authority.

The active registry uses schema 2 and canonical registry identity
`fab02c05906bcca0ed33cc483465640e2348e98bef8c7ad01460c23da3eac7c1`; it contains two
`locally-approved` and five `proposed` records and no `accepted-in-ncit` record
(`pdm run agent-test ontolib/tests/decomposition/test_proposal_registry.py::test_tracked_proposal_registry_remains_valid -v`,
2026-09-07). Schema 2 adds the closed typed `adoption_evidence` field and rejects schema 1 rather
than interpreting it. The deterministic atomic current-schema writer can canonicalize an already
valid registry in place without changing its bytes:

```bash
pdm run adjudication write-proposal-registry ontolib/tests/decomposition/golden/proposal-registry.json
```

The writer's two-output and in-place determinism are exercised by
`pdm run agent-test ontolib/tests/decomposition/test_proposal_registry.py::test_registry_writer_is_canonical_atomic_and_deterministic -v`
(2026-09-07). The registry identity above was bound after the schema-2 writer generated the
payload; the current evidence envelopes were then regenerated, while the historical human
adjudication and R103 artifacts remained immutable and are checked through the separate migration
binding
(`pdm run agent-test ontolib/tests/decomposition/test_current_evidence.py ontolib/tests/decomposition/test_r103_review.py ontolib/tests/decomposition/test_r103_review_promotion.py ontolib/tests/decomposition/test_r103_terminal_revision.py -v`,
2026-09-07).

`proposal-registry-schema2-migration.json` is the append-only machine binding from the exact
schema-1 registry identity and file digest referenced by the historical evidence to the active
schema-2 registry. It records every proposal ID, kind, status, source reference, and subject
semantics identity; binds the unchanged human-decision artifacts byte-for-byte; and states both
that no proposal transitioned to `accepted-in-ncit` and that NCI adoption was not inferred. Generate
it only after all five named inputs and the output parent exist (`ls
ontolib/tests/decomposition/golden/neoplasm-adjudicated.json
ontolib/tests/decomposition/golden/r103-review-state-26.07d.json
ontolib/tests/decomposition/golden/r103-review-state-26.07d-rev2.json
ontolib/tests/decomposition/golden/r103-c3264-corroboration-26.07d.json
ontolib/tests/decomposition/golden/proposal-registry.json tmp`, 2026-09-07):

```bash
pdm run adjudication bind-proposal-registry-migration \
  --historical-oracle ontolib/tests/decomposition/golden/neoplasm-adjudicated.json \
  --historical-r103-review ontolib/tests/decomposition/golden/r103-review-state-26.07d.json \
  --historical-r103-revision ontolib/tests/decomposition/golden/r103-review-state-26.07d-rev2.json \
  --historical-r103-corroboration ontolib/tests/decomposition/golden/r103-c3264-corroboration-26.07d.json \
  --current-registry ontolib/tests/decomposition/golden/proposal-registry.json \
  --output ontolib/tests/decomposition/golden/proposal-registry-schema2-migration.json
```

Two independent generations matched each other and the tracked bytes (`cmp
tmp/proposal-registry-schema2-migration-first.json
tmp/proposal-registry-schema2-migration-second.json` and `cmp
tmp/proposal-registry-schema2-migration-first.json
ontolib/tests/decomposition/golden/proposal-registry-schema2-migration.json`, 2026-09-07). The
tracked envelope SHA-256 is `73b135ffcafa7efee6617a52d9037c51bf32ee9ba66f3f1bf7d1492a3ed5a6b3`
(`shasum -a 256
ontolib/tests/decomposition/golden/proposal-registry-schema2-migration.json`, 2026-09-07).

The compressed review registry has schema 3/status `proposed`, identity
`358b42f8279c067fbd0543572073cd5f6887eea0dc74d148483328c02ceb6975`, and exactly
3,291 atomic rows partitioned into 3,288 `approved-non-exclusive-coverage` and three
`rejected-retain-broader` outcomes; all 2,800 disease-exception values are false
(`pdm run agent-test ontolib/tests/decomposition/test_collapse_veto_policy.py::test_tracked_registry_golden_has_exact_authorized_accounting -v`,
2026-08-20). The three rejections are operational collapse vetoes only: their broader source
sites remain review-required alongside Frontal Sulcus (C32639), while complete source facts,
equivalence quarantine, publication state, and NCIt adoption state remain unchanged.

All of these files are tracked (`git ls-files ontolib/tests/decomposition/golden`, 2026-08-09).
The oracle status, NCIt version, reviewer, concept count, and expected-pair count come directly
from the tracked oracle
(`jq '{status:._meta.status,ncit_version:._meta.ncit_version,reviewer:._meta.reviewer.name,concepts:(.concepts|length),expected_pairs:([.concepts[]|select(.expected!=null)|.expected.constituents[]]|length)}' ontolib/tests/decomposition/golden/neoplasm-adjudicated.json`,
2026-08-09). The row export and oracle identify the same source workbook with digest
`c1fbcb0d3d09c4846b519bc9e58fced4b393461191c2634994d2346f9df12321`
(`jq -n --slurpfile rows ontolib/tests/decomposition/golden/neoplasm-row-decisions.json --slurpfile oracle ontolib/tests/decomposition/golden/neoplasm-adjudicated.json '{row_export:$rows[0]._meta.workbook_identity,oracle:$oracle[0]._meta.workbook_identity,equal:($rows[0]._meta.workbook_identity==$oracle[0]._meta.workbook_identity)}'`,
2026-08-09). This is the source identity recorded by tracked artifacts; no untracked workbook
path is evidence for the baseline.

## The M1 baseline this oracle records

The focused baseline test recomputes the published values from the tracked measurement inputs
above (`pdm run pytest ontolib/tests/decomposition/test_m1_baseline.py -q`, 2026-08-09).

**Pair-level** — the oracle's expected pairs against the recorded engine run:

| Metric | Tracked value |
|---|---|
| precision / recall (`ncit_bound`, D59 strict denominator) | 0.7547 / 0.5229 |
| true positives / emitted pairs | **80 / 106** |
| false positives / false negatives (`ncit_bound`) | **26 / 73** |
| relationship-group agreement | **2 of 20 concepts** |
| `residual_precoordination` — adjudication / sample | 18/18 and 13/13, delta 0.0 |

The baseline tests pin 153 NCIt-bound expected pairs, both rounded ratios, group agreement, and
both residual fractions
(`pdm run pytest ontolib/tests/decomposition/test_m1_baseline.py::test_expected_pair_provenance_holds_the_m1_baseline ontolib/tests/decomposition/test_m1_baseline.py::test_ncit_bound_precision_and_recall_hold_the_m1_baseline ontolib/tests/decomposition/test_m1_baseline.py::test_group_partition_agreement_holds_the_m1_baseline ontolib/tests/decomposition/test_m1_baseline.py::test_residual_comparison_holds_the_m1_baseline -q`,
2026-08-09). True positives are uniquely 80 because 80 is the only integer `tp` for which
`round(tp / 153, 4) == 0.5229`; the remaining counts follow as `106 - 80 = 26` and
`153 - 80 = 73`
(`pdm run python -c 'print([tp for tp in range(154) if round(tp/153,4)==0.5229])'`,
2026-08-09).

**Row-level** — the SME labels on the 189 selected decision rows
(`jq '.rows|length' ontolib/tests/decomposition/golden/neoplasm-row-decisions.json`,
2026-08-09):

| | `include` | `revise` | `exclude` | `not-needed` |
|---|---|---|---|---|
| `ENGINE SUGGESTION` (106 rows) | **48** | **42** | **16** | — |
| `ADD IF MISSING` (83 rows) | **63** | 1 | 4 | 15 |

The cross-tab counts come directly from the tracked row type and SME action fields
(`jq '.rows|group_by([.row_type,.sme_action])|map({row_type:.[0].row_type,sme_action:.[0].sme_action,count:length})' ontolib/tests/decomposition/golden/neoplasm-row-decisions.json`,
2026-08-09).

The 48/106 value is strictly the SME `include`-label rate. It is not an unchanged-row,
kept-as-offered, or no-revision rate: 11 of the 48 included rows differ from the recorded
engine constituent in relationship-group or `needs_review` fields
(`pdm run python -c 'import json,pathlib; p=pathlib.Path("ontolib/tests/decomposition/golden"); r=json.loads((p/"neoplasm-row-decisions.json").read_text())["rows"]; e=json.loads((p/"neoplasm-engine-evidence.json").read_text()); a=json.loads((p/"neoplasm-adjudicated.json").read_text()); E={(c["code"],x["axis"],x["filler"]):x for c in e["concepts"] for x in c["constituents"]}; A={(c["code"],x["axis"],x["filler"]):x for c in a["concepts"] if c["expected"] for x in c["expected"]["constituents"]}; I=[x for x in r if x["row_type"]=="ENGINE SUGGESTION" and x["sme_action"]=="include"]; print({"include":len(I),"changed_group_or_review":sum(any(E[(x["code"],x["expected"]["axis"],x["expected"]["filler"])].get(f)!=A[(x["code"],x["expected"]["axis"],x["expected"]["filler"])].get(f) for f in ("relationship_group","needs_review")) for x in I)})'`,
2026-08-09).

Among 90 kept engine-suggestion rows, exact `(axis, filler)` pairs match on 80, fillers match
on 87, and axes match on 83
(`jq '[.rows[]|select(.row_type=="ENGINE SUGGESTION")] as $r | [$r[]|select(.sme_action=="include" or .sme_action=="revise")] as $k | {suggestions:($r|length),kept:($k|length),pair_match:([$k[]|select(.engine==.expected)]|length),filler_match:([$k[]|select(.engine.filler==.expected.filler)]|length),axis_match:([$k[]|select(.engine.axis==.expected.axis)]|length)}' ontolib/tests/decomposition/golden/neoplasm-row-decisions.json`,
2026-08-09). Exact pair preservation is not an independent filler-accuracy measurement; report
these separately.

Candidate rows have 63 `include` labels but 64 kept constituents because one `revise` row is
also kept. Its revised pair for Stage I Endometrial Cancer FIGO 2023 (`C206219`),
`op:PrimarySite` Corpus Uteri (`C12316`), is absent from the recorded engine
evidence
(`jq -n --slurpfile rows ontolib/tests/decomposition/golden/neoplasm-row-decisions.json --slurpfile engine ontolib/tests/decomposition/golden/neoplasm-engine-evidence.json '[$rows[0].rows[]|select(.row_type=="ADD IF MISSING")] as $r | {include:([$r[]|select(.sme_action=="include")]|length),kept:([$r[]|select(.sme_action=="include" or .sme_action=="revise")]|length),revised:([$r[]|select(.sme_action=="revise")|. as $x|{code,expected,in_engine:any($engine[0].concepts[];.code==$x.code and any(.constituents[];.axis==$x.expected.axis and .filler==$x.expected.filler))}])}'`,
2026-08-09).

The `ENGINE SUGGESTION` / `not-needed` combination is absent from the typed union and from the
cross-tab result
(`pdm run pytest ontolib/tests/decomposition/test_golden_review.py::test_engine_suggestion_cannot_be_left_not_needed ontolib/tests/decomposition/test_golden_review.py::test_the_cell_no_engine_suggestion_row_can_occupy_has_no_field -q`,
2026-08-09).

The 154 kept row triples equal the oracle expectations, and the 106 recorded suggestions equal
the engine emissions
(`pdm run pytest ontolib/tests/decomposition/test_m1_baseline.py::test_row_decisions_and_the_oracle_agree_on_the_expected_set ontolib/tests/decomposition/test_m1_baseline.py::test_the_acceptance_denominator_is_this_engine_run_s_output -q`,
2026-08-09). Excluded rows contribute no kept expectation; 3 of the 20 excluded rows carry a
withdrawn expectation, while 17 name none
(`jq '{excluded:([.rows[]|select(.sme_action=="exclude")]|length),withdrawn:([.rows[]|select(.sme_action=="exclude" and .expected!=null)]|length),unnamed:([.rows[]|select(.sme_action=="exclude" and .expected==null)]|length)}' ontolib/tests/decomposition/golden/neoplasm-row-decisions.json`,
2026-08-09).

The three withdrawn expectations are pinned by the baseline test
(`pdm run pytest ontolib/tests/decomposition/test_m1_baseline.py::test_three_withdrawn_expectations_sit_outside_the_oracle -q`,
2026-08-09):

| Concept | Withdrawn source-row expectation | Disposition |
|---|---|---|
| Stage III Thyroid Gland Medullary Carcinoma AJCC v7 (`C6135`) | `op:AssociatedRegion` Head and Neck (`C12418`) | engine suggestion, excluded |
| Stage I Differentiated Thyroid Gland Carcinoma Under 45 Years AJCC v7 (`C101539`) | `op:AssociatedRegion` Head and Neck (`C12418`) | candidate, excluded |
| Left Atrial Myxoma (`C4791`) | `op:AssociatedRegion` Heart (`C12727`) | candidate, excluded |

The last row records that Heart (`C12727`) as `op:AssociatedRegion` was withdrawn; it does not
make Heart the primary site. The adjudicated primary site for Left Atrial Myxoma (`C4791`) is
Left Atrium (`C12869`). Endocardium (`C13004`) is tissue rather than the primary site and is
retained on both `op:AssociatedRegion` and `op:AssociatedSite` because D23 permits the same
anatomy on multiple axes.

Blanking those three `expected` fields leaves the cross-tab unchanged but removes the recorded
withdrawal evidence; the test above pins the evidence relation. The unchanged cross-tab and the
loss of three named withdrawals reproduce in memory without writing an artifact
(`pdm run python -c 'import json,pathlib; from scripts.research.golden_review import RowDecisionExport,_payload_identity; d=json.loads(pathlib.Path("ontolib/tests/decomposition/golden/neoplasm-row-decisions.json").read_text()); d["rows"]=tuple(d["rows"]); before=RowDecisionExport.model_validate(d).cross_tab(); named_before=sum(x["sme_action"]=="exclude" and x.get("expected") is not None for x in d["rows"]); [x.__setitem__("expected",None) for x in d["rows"] if x["sme_action"]=="exclude"]; d["payload_identity"]=_payload_identity({k:v for k,v in d.items() if k!="payload_identity"}); after=RowDecisionExport.model_validate(d).cross_tab(); named_after=sum(x["sme_action"]=="exclude" and x.get("expected") is not None for x in d["rows"]); print({"cross_tab_equal":before==after,"named_withdrawals_before":named_before,"named_withdrawals_after":named_after})'`,
2026-08-09). Do not attribute a cross-tab rate change to blanking withdrawn `expected` fields.

The 15-code sample is a strict subset of the 20-code oracle cohort
(`jq -n --slurpfile sample samples/ncit-26.07d-m1-review.json --slurpfile oracle ontolib/tests/decomposition/golden/neoplasm-adjudicated.json '($sample[0].concepts|map(.code)) as $s|($oracle[0].concepts|map(.code)) as $o|{sample:($s|length),oracle:($o|length),sample_only:($s-$o),oracle_only:($o-$s)}'`,
2026-08-09), so the tracked residual comparison is not an independent-population divergence
test (D62).

## How adjudication works

Treat `AUTO-DRAFT` artifacts as review inputs, never as an oracle. Review source evidence,
record the SME disposition, bind completed artifacts after generation, score only a valid
`SME-ADJUDICATED` artifact, and never edit the oracle merely to match an engine result.

The tracked oracle declares schema version 3, `SME-ADJUDICATED`, 20 concepts, and the required
cohort concepts Left Atrial Myxoma (`C4791`), Stage IIIB Lung Small Cell Carcinoma with Pleural
Effusion AJCC v7 (`C35756`), and Stage III Colon Cancer AJCC v7 (`C89995`)
(`jq '{schema_version:._meta.schema_version,status:._meta.status,concepts:(.concepts|length),required_codes:([.concepts[].code]|map(select(.=="C4791" or .=="C35756" or .=="C89995")))}' ontolib/tests/decomposition/golden/neoplasm-adjudicated.json`,
2026-08-09). Loader tests cover draft rejection, metadata, outcome, constituent, proposal, and
identity validation
(`pdm run pytest ontolib/tests/decomposition/test_golden_review.py -q`, 2026-08-09).

Scoring exposes NCIt-bound, augmented, defining, and non-defining views, with typed outcome and
proposal rules enforced by the loader
(`pdm run pytest ontolib/tests/decomposition/test_golden_review.py -k 'provenance or modality or outcome or proposal' -q`,
2026-08-09). Residual comparison inputs record the exact denominator and residual code lists
(`jq '{denominator_codes,residual_codes}' ontolib/tests/decomposition/golden/neoplasm-corpus-comparison.json`,
2026-08-09).

The CLI surface is documented by its own help output (`pdm run adjudication --help`,
2026-08-09). This README deliberately gives no regeneration command for the tracked oracle or
row export: an executable regeneration step would require naming every concrete input and proving
that each exists, and `git ls-files '*.xlsx'` returns no tracked workbook path (2026-08-09).

`import-workbook` projects kept decisions (`include` and `revise`) into the oracle;
`exclude` and `not-needed` contribute no kept expectation. Some excluded rows still carry the
expectation the reviewer withdrew, as the three-row baseline test proves
(`pdm run pytest ontolib/tests/decomposition/test_m1_baseline.py::test_three_withdrawn_expectations_sit_outside_the_oracle -q`,
2026-08-09). `export-row-decisions` writes a selected row-decision projection, not verbatim
workbook rows: each tracked row contains only `code`, `engine`, `expected`, `row_type`, and
`sme_action` as applicable
(`jq '[.rows[]|keys]|unique' ontolib/tests/decomposition/golden/neoplasm-row-decisions.json`,
2026-08-09). The export path applies:

- the **workbook-level validation gates** — sheet contract, sheet visibility, hidden reviewer
  rows and columns, formula cells, attestation, required evidence keys;
- the **`Concept Decisions` preconditions**, shared with the import so the two cannot
  diverge — required headers, no hidden concept row, no populated row with a blank code —
  followed by the orphan check, so a constituent row naming a concept the reviewer never
  declared, or declared only on a concealed row, cannot inflate the denominator;
- the **shared constituent row reader** — concept code, row type, SME action vocabulary,
  no `PENDING` engine suggestion, `Row Complete?` (waived for a `not-needed` row, which an
  engine suggestion can never be), and canonical `Expected Axis`/`Expected Filler`;
- the **row-shape gates** — an expected pair names both an axis and a filler or neither, a
  `not-needed` row names no expected pair, and `Engine Axis`/`Engine Filler` are present
  and canonical exactly on `ENGINE SUGGESTION` rows.

The focused export tests exercise these workbook, concept-row, constituent-row, and engine-pair
boundaries
(`pdm run pytest ontolib/tests/decomposition/test_golden_review.py -k 'row_decision_export' -q`,
2026-08-09).

It does **not** run the kept-constituent gates — `Expected Provenance Status`,
`Expected needs_review`, `Expected Group`, `Expected Proposal ID` — nor the cohort or
proposal-registry gates; those belong to `import-workbook`. A workbook the export accepts
can still be rejected as an oracle
(`pdm run pytest ontolib/tests/decomposition/test_golden_review.py::test_row_decision_export_accepts_kept_constituent_defects_the_import_rejects -q`,
2026-08-09).

The export carries a `payload_identity` and `_meta` names `source_identity`, `run_id`, and
`engine_evidence_identity`
(`jq '{payload_identity,source_identity:._meta.source_identity,run_id:._meta.run_id,engine_evidence_identity:._meta.engine_evidence_identity,schema_version:._meta.schema_version}' ontolib/tests/decomposition/golden/neoplasm-row-decisions.json`,
2026-08-09). `payload_identity` is an unkeyed self-consistency digest stored inside the
document it covers. It detects an edit only when the editor does not recompute the digest; it
does not establish the payload's origin or author. The focused loader test proves that narrow
edit-detection property
(`pdm run pytest ontolib/tests/decomposition/test_golden_review.py::test_row_decision_loader_rejects_a_hand_edited_row_set -q`,
2026-08-09). Separately, the baseline test proves that the 106 recorded suggestion triples equal
the emitted triples in `neoplasm-engine-evidence.json`
(`pdm run pytest ontolib/tests/decomposition/test_m1_baseline.py::test_the_acceptance_denominator_is_this_engine_run_s_output -q`,
2026-08-09).

A row is one of three shapes, discriminated on `sme_action`, so the invariants are carried
by the type rather than by a validator: a **kept** row (`include`/`revise`) must name an
`expected` pair; an **excluded** row either names the expectation withdrawn or names
nothing — a half-named withdrawal is unrepresentable; an **unused candidate**
(`not-needed`) is `ADD IF MISSING` only and carries no expected pair. Every row also
carries `engine`, non-null exactly when `row_type` is `ENGINE SUGGESTION`, enforced as a
biconditional. The combination `ENGINE SUGGESTION` + `not-needed` has no inhabitant — an
engine suggestion the reviewer never ruled on cannot reach the denominator
(`pdm run pytest ontolib/tests/decomposition/test_golden_review.py::test_engine_suggestion_cannot_be_left_not_needed ontolib/tests/decomposition/test_golden_review.py::test_the_cell_no_engine_suggestion_row_can_occupy_has_no_field -q`,
2026-08-09).

`cross_tab()` returns the typed engine and candidate outcomes; the tracked result has
`included_rate = 0.4528`, 80 exact pair matches, and 64 kept candidate rows
(`pdm run python -c 'from pathlib import Path; from scripts.research.golden_review import load_row_decisions; x=load_row_decisions(Path("ontolib/tests/decomposition/golden/neoplasm-row-decisions.json")).cross_tab(); print({"included_rate":round(x.engine_suggestion.included_rate or 0,4),"pair_match":x.engine_suggestion.pair_preserved,"kept_candidates":x.add_if_missing.include+x.add_if_missing.revise})'`,
2026-08-09).

The tracked artifacts record the source workbook identity; checking the baseline does not
require a workbook path
(`pdm run pytest ontolib/tests/decomposition/test_m1_baseline.py -q`, 2026-08-09). Regeneration
is intentionally not documented as an executable step here because no tracked workbook input
exists; bind any future generated artifact only after every concrete input exists (D61).

## Current-source replay and corpus baseline

These commands generate new current-source evidence beside the immutable historical files. They
do not regenerate or modify the SME oracle or row decisions.

The implementation-agent entry point checks every fixed input before execution and invokes no
shell. Read the issue contract through the same narrow entry point:

```bash
pdm run agent-replay read-issue 274
```

Run creation remains a separate operation. A refused fresh run is never changed into an implicit
resume, and a complete, published run cannot be resumed. The completed-run replay route was
therefore removed rather than copying or rebinding bytes as though current decomposition code had
executed.

Generate the final downstream evidence candidate from an existing immutable replay generation.
The supplied manifest identity certifies the exact historical replay bytes; the wrapper also
checks those bytes against the run's currently persisted published representation. The replay's
generator may be an older repository identity. Only the downstream evidence generator executes at
the current repository identity, so the resulting chain does not claim that decomposition was
rerun with current code:

```bash
pdm run agent-replay generate-current-evidence-candidate \
  tmp/artifacts/v1/generations/m1-6-current-replay/<generation-id>/manifest.json \
  <manifest-identity>
```

Supply the exact existing manifest path and its computed identity at execution time; placeholders
are not accepted. The retired run-ID-only form completed against the persisted
Postgres run on 2026-09-07 after
`ls -l tmp/m1-6-current-replay.ttl` established the published input existed. It regenerated bytes
with SHA-256 `0705d725eafa1c49e95a1cb0885c90657bc84078f584ca153102cf3d5fa8fb4c` for
`neoplasm-current-engine-evidence.json`,
`8c675e3fde38b6c27f52cce9ec65fdb0a8a9af98bad23fac436313df75793a6a` for
`neoplasm-current-comparison.json`, and
`b049cafa8fc912db0239e08cc2206eb263fdee8be7d53fb4133f8ee49e960e9e` for the published Turtle
(`shasum -a 256 ontolib/tests/decomposition/golden/neoplasm-current-engine-evidence.json
ontolib/tests/decomposition/golden/neoplasm-current-comparison.json
tmp/m1-6-current-replay.ttl`, 2026-09-07). `git diff --no-ext-diff --` followed by both current
artifact paths returned no output after generation, proving that the regenerated bytes equal the
tracked bytes (2026-09-07).

The historical human and historical engine inputs remained byte-identical before and after that
generation. The before and after `shasum -a 256` commands returned, respectively:
`b3e909802ddc762d3c348c19ac25f343cc888d9e2ec29108bd94b02c89657509`
(`neoplasm-adjudicated.json`),
`f8f32483ba8b438e15a5ebe83a885f3de9f3ba34e37de3e0aef19e541be85cc9`
(`neoplasm-row-decisions.json`),
`42e33238c7b18985263f780a165ad42d1230bb620a2aac8edf11748cf661f74f`
(`neoplasm-engine-evidence.json`),
`34c34d671e77ec041f0d66ace73a2c9a5fcb7fd77134c2d3fa0fb1036f3b3ff5`
(`neoplasm-corpus-comparison.json`),
`3b17fee5ac354ca8d48637f2a7f8b0451e0b4afed6922d0f745e6d284ca9c899`
(`r103-review-state-26.07d.json`),
`03822dcbfc4190e09e9394cb310aae2a6cca2f9c8d728bf3997d9e11d1e4730f`
(`r103-review-state-26.07d-rev2.json`), and
`a1d4b82f985d6fc099040491ac3ad4d40231452265efc10c2b8ac1c43519c823`
(`r103-c3264-corroboration-26.07d.json`) (the same exact seven-path `shasum -a 256` command before
and after generation, 2026-09-07). A path-scoped `git diff --no-ext-diff --` over those seven files
also returned no output afterward (2026-09-07).

The generator derives every identity from those inputs and the completed persisted run. It refuses
source, release, manifest, worklist, run, fingerprint, artifact, representation, detector, oracle,
row-decision, registry, migration-envelope, or evidence drift before replacing either output.
Occurrence identity is retained through Postgres provenance and these current evidence/report
outputs. The decomposed RDF/read API remains intentionally fact-level under #9 and cites complete
source fact IDs rather than occurrence IDs; that reporting boundary does not permit occurrence
erasure before persistence or current reporting.

`neoplasm-highest-fanout.json` records the exhaustive maximum only within the stated-genus
subclass scope rooted at C3262 (15,633 neoplasm concepts scanned). It is not an all-NCIt maximum.

Generate the current axis diagnostics for the three explicit residual-detector branches (detected,
not detected, and proposed filler absent from source):

```bash
pdm run agent-replay generate-axis-diagnostics C35501 C12431 MINT-781c8c8c6096
```

The active normalized-group policy covers each current output pair with exact source-fact evidence.
Restriction evidence uses occurrence citations when available and otherwise records the exact
available source fact and coordinate; genus facts are explicitly non-occurrence evidence. Historical
expected partitions remain separately identified review context. The evidence sheets display exact
source facts, source groups, occurrences where applicable, anchors, depth/path, and transformation
witnesses. Machine evidence is never reviewer rationale.

The tracked historical admission preserves the completed Markdown verbatim at
`evidence/group-review-rationale-26.07d.md`; its JSON sidecar is digest/operational binding only,
and `evidence/group-review-packet-26.07d-schema3.json` preserves the exact schema-3 machine context.
Schema 3 did not distinguish scoreable release-bound pairs from review-bearing emitted pairs, so
the old review is historical context rather than an active decision registry. Generate fresh axis
diagnostics instead of transcribing it. The immutable candidate chain supplies the blank schema-4
review boundary.

```bash
pdm run agent-replay generate-axis-diagnostics C35501 C12431 MINT-781c8c8c6096
```

The historical record contains 11 corrections and 4 escalations. The scoped current policy resolves
the #274 normalized-group targets without treating historical context as current authorization.
The broader total-delta classification remains open under #127 and publication remains unauthorized.

### Group-review candidate

The immutable group-review candidate contains the packet, workbook, pair-relation audit, and blank
validation. The workbook leaves Pair Decision, Decision, Rationale, Reviewer, and Date blank, so
candidate generation records no SME adjudication.

The group-review rule-evidence audit is deliberately narrow:

| Rule evidence kind | Existing producer and exact fields consumed |
|---|---|
| co-assertion preservation | `generate_current_evidence()` → `CurrentConceptEvidence.all_source_occurrences` and `CurrentConstituent.source_occurrences`, retaining `source_group_id`, `source_fact_id`, and `occurrence_id` beside normalized output-group identities |
| routing | `generate_current_evidence()` → `CurrentConstituent.axis` plus each cited occurrence's `role_code`, `filler_code`, source fact, source group, and occurrence identity |
| specificity collapse | `generate_current_evidence()` → exact `CurrentOccurrenceDisposition` rows whose kind is `collapsed-r82`, joined to current source occurrences and output groups |
| repeated pairs | `generate_current_evidence()` → the complete `CurrentConstituent.source_occurrences` set for one normalized axis/filler pair |
| reviewed regrouping | `validate_current_comparison()` → current/expected partitions and grouping diagnosis, joined to current source occurrences and output groups; the historical expected partition is explicitly labelled as lacking source citations |

The candidate resolves its current evidence and current comparison from its exact parent manifest.

### R103 manual SME review boundary (#294)

Generate the source-bound packet and blank workbook offline from the pinned stated OWL
artifact and the existing proposal registry (no QLever or database connection):

```bash
pdm run agent-replay generate-r103-review
```

This command remains the upstream staging producer for historical promotion and
reproduction. Its standalone packet remains meaningful as the source-bound input to
that path even though current machine readiness now consumes the packet embedded in
the tracked promoted state rather than the `tmp/` packet directly.

The operation requires all four inputs and writes only
`tmp/m1-6-r103-review-packet.json` and
`tmp/m1-6-r103-review-workbook.xlsx`. The workbook is intentionally untracked and its
four SME fields (`Outcome`, `Rationale`, `Reviewer`, `Date`) are blank. After a human
reviews the workbook, import and dry-run with every source/governance input named:

```bash
pdm run python scripts/adjudication.py import-r103-review-decisions --packet tmp/m1-6-r103-review-packet.json --reviewed-xlsx tmp/m1-6-r103-review-workbook-reviewed.xlsx --output tmp/m1-6-r103-review-decisions.json
pdm run python scripts/adjudication.py dry-run-r103-review --packet tmp/m1-6-r103-review-packet.json --registry tmp/m1-6-r103-review-decisions.json --oracle ontolib/tests/decomposition/golden/neoplasm-adjudicated.json --proposal-registry ontolib/tests/decomposition/golden/proposal-registry.json --output tmp/m1-6-r103-review-dry-run.json
```

The importer produces a distinct R103 review-evidence registry. It does not mutate the
oracle or proposal registry; a later accepted correction proposal must extend the existing
`ProposalRegistry` through its separate governance path.

The historical local-SME state is durably composed in
`r103-review-state-26.07d.json`. It remains `review-incomplete`, unresolved by one
decision, and write-free. This file is a deterministic test golden, not runtime package
data, accepted NCIt content, publication authorization, or permission to apply a change.
It embeds the complete packet, decision registry, and dry-run so strict loading and nested
identity validation do not read `tmp/` after the original staging files disappear. Loading
the golden does not claim to recompute the file-dependent dry-run; recomputation occurs
only during the original promotion operation.

The original promotion requires the untracked or archived packet, registry, dry-run,
oracle, and proposal-registry staging inputs. There is no claim that the tracked golden
alone can regenerate that historical promotion. The following generation and promotion
commands are historical reproduction steps; promotion was generated and then rerun as a
byte-identical no-op with:

```bash
pdm run adjudication promote-r103-review-state --packet tmp/m1-6-r103-review-packet.json --registry tmp/m1-6-r103-review-decisions.json --dry-run tmp/m1-6-r103-review-dry-run.json --oracle ontolib/tests/decomposition/golden/neoplasm-adjudicated.json --proposal-registry ontolib/tests/decomposition/golden/proposal-registry.json --output ontolib/tests/decomposition/golden/r103-review-state-26.07d.json
```

Its self-excluding `artifact_identity` is
`90ea507e93cebaf6399b3aa5bea92081e6d3dba50b7631783666d9382d267d1a`
(`pdm run agent-test ontolib/tests/decomposition/test_r103_review_promotion.py::test_tracked_promotion_loads_with_exact_review_and_noncohort_oracle_boundary -v`,
2026-08-27). The oracle binding covers bytes only and implies no cohort relationship.
The same test freshly verifies that C2860, C3264, and C3716 are not members of the
20-code oracle cohort. An oracle byte change requires a distinct new promotion record;
it must never refresh or overwrite this historical record.

The append-only terminal revision is
`r103-review-state-26.07d-rev2.json`; rev1 remains byte-identical and is never selected by
a fallback or latest-file scan. The revision binds rev1 as predecessor, the embedded source
packet, the effective workbook decisions, and software transcription provenance while preserving
human authority and claiming no software authorship. The exact human rationale is stored only in
the C3264 decision. A separate machine qualification records that R103 is non-defining, the
exclusion is exact to C3264/R103/C12950, and descendants may have specific embryonic or fetal
origins.

The governed reconstruction, transcription, and promotion commands are:

```bash
pdm run adjudication prepare-r103-review-revision --predecessor ontolib/tests/decomposition/golden/r103-review-state-26.07d.json --output-xlsx tmp/m1-6-r103-review-revision-blank.xlsx
pdm run adjudication transcribe-r103-review-revision --predecessor ontolib/tests/decomposition/golden/r103-review-state-26.07d.json --blank-xlsx tmp/m1-6-r103-review-revision-blank.xlsx --output-xlsx tmp/m1-6-r103-review-revision-transcribed.xlsx --subject C3264 --role R103 --filler C12950 --outcome concept-scoped-accuracy-exclusion --rationale-file tmp/r103-terminal-rationale.json --reviewer "R. Hannes Niedner, M.D." --review-date 2026-08-28
pdm run adjudication promote-r103-review-revision --predecessor ontolib/tests/decomposition/golden/r103-review-state-26.07d.json --reviewed-xlsx tmp/m1-6-r103-review-revision-transcribed.xlsx --oracle ontolib/tests/decomposition/golden/neoplasm-adjudicated.json --proposal-registry ontolib/tests/decomposition/golden/proposal-registry.json --output-registry tmp/m1-6-r103-review-revision-decisions.json --output-dry-run tmp/m1-6-r103-review-revision-dry-run.json --output ontolib/tests/decomposition/golden/r103-review-state-26.07d-rev2.json
```

The historical promotion reported revision identity
`d99b3f27bb2d6416149411ecbe13893aed88d183f39405acd80529c771a5d160` and historical
corroboration identity `f96081372e6d7e3be0e65a5ab8342b12f5b5d129df16b263db5dbafe6130552c`.
Those bytes are preserved, but the historical PubMed authority/verification claim is not current
evidence because no response bytes were retained.
The current machine path is generated with:

```bash
pdm run agent-replay generate-r103-evidence-application
```

It writes the source-only inventory, the complete 16-row named stated C12950 descendant
enumeration, normalized authority, downgraded reviewer-reference corroboration, applied-policy
report, strict C2860 specificity target, and unanswered C2860 specificity-review state. The target
binds the candidate artifact only to the exact C2860/R103/C12950 source occurrence and its
carried-forward decision. The applied report remains unchanged: it preserves the official source assertions, suppresses only
C3264/R103/C12950 in the effective projection, retains C2860 and C3716, leaves the schema-2
proposal registry and oracle unchanged, creates no proposal, infers no NCI adoption, and leaves
authorization false. Candidate generation cannot reopen that terminal C3264 exclusion.

The C2860 question asks whether one of the 16 enumerated named stated descendants of C12950 in
NCIt 26.07d provides a better normal-tissue-origin filler than C12950. Its exact choices are:

1. affirm that none of those bounded 16 candidates is better;
2. retain source-supported C2860/R103/C12950 while qualifying or withdrawing the global
   most-specific claim; or
3. select one enumerated existing NCIt candidate as a proposed replacement and initiate a
   separately governed correction proposal.

The pending artifact selects none of these choices and records no software-authored human decision.
The accountable user selected choice 2 on 2026-09-07: retain source-supported
`C2860/R103/C12950`, record that none of the bounded 16 candidates is better, and withdraw the
global-most-specific rationale because the bounded comparison cannot establish global NCIt
optimality. Generate its strict successor from the named pending and machine inputs with:

```bash
pdm run agent-replay transcribe-r103-specificity-selection
```

The selected artifact binds the target, candidate artifact, prior carried-forward decision, and
unchanged applied-policy report. It records software as transcriber rather than author, selects no
replacement candidate, creates no proposal, and infers no NCI adoption
(`pdm run agent-replay transcribe-r103-specificity-selection`, 2026-09-07). The pending artifact is
retained as the exact pre-selection input, not as current readiness state.

### Final machine-readiness evidence

Capture the exact verification gate only from a clean worktree with the valid rootless
`ontoprism-vm` running and the selected Docker context set to `ontoprism-podman` at that
machine's inspected socket:

```bash
pdm run agent-replay capture-pre-sme-verify
```

The operation observes the clean Git HEAD before and after running the literal
`pdm run verify`, requires the gate to pass without changing the worktree, and writes
`tmp/m1-6-verify-evidence.json`. The evidence records the inspected Podman endpoint,
selected context, resolved PDM executable/version, exit code, and bound Git HEAD. It is
local machine evidence and performs no ontology or store publication.

After the current comparison, the explicit tracked historical row decisions, R101 reuse validation, primary-site audit, tracked
R103 promoted state, full-corpus baseline/artifact, proposal registry, source manifest, selected R103 specificity state, and
current-HEAD verify evidence all exist,
generate the pending-human
report from a clean worktree:

```bash
pdm run agent-replay generate-pre-sme-readiness \
  tmp/artifacts/v1/generations/m1-6-grouping-detector-candidate/<generation-id>/manifest.json \
  <exact-manifest-identity>
```

Readiness strictly loads the complete promoted R103 state, validating its embedded
packet, registry, dry-run, decision vector, and cross-bindings. It also loads the strict
C2860 specificity-review state. Changed or malformed registry, dry-run, target, or review
state fails readiness closed. C3264 remains terminally excluded and C3716 remains covered
by its prior terminal decision; only the C2860 specificity question is pending. A future
human-selected review state satisfies that one requirement while retaining the same source,
target, and candidate evidence identities.

The operation resolves the exact schema-5 group-review packet through the detector manifest's parent
bindings instead of a mutable fixed `tmp/` packet path. It validates all remaining fixed input
identities and cohort invariants, including the
row-decision identity that supplies the immutable historical 48/106 SME include rate,
refuses verify evidence from another Git HEAD, and atomically writes
`tmp/m1-6-machine-readiness.json`. Schema 3 reports the five named metric contracts,
the strict M1.6 improvement gate, the separate #44 quality indicators, and one canonical
entry for each semantic blocker. The identity-bound #274 axis-contract, normalized-group,
and unadjudicated-golden-change detectors are evaluated; the broader total-delta classifier
remains `not-evaluated` under #127. Any evaluated violation produces a blocked report rather
than publication authorization. The output
always records authorization false and publication `not-attempted`
(`pdm run agent-test ontolib/tests/decomposition/test_pre_sme_readiness.py -v`,
2026-09-06).

The two-run R101 diagnostic and review tooling was removed in #341 after its decisions were
transcribed into packaged policy data. Readiness reports D74 unexplained R101 loss as
`not-evaluated`, owned by #417, until that issue adds the per-run conservation check. The broader
total-delta classifier remains `not-evaluated` under #127.

Generate the exhaustive fanout observation against the configured current source:

```bash
pdm run python scripts/observe_decomposition_fanout.py \
  --endpoint http://localhost:7888 \
  --source-manifest data/qlever-ncit/.ontoprism-ncit-candidate.json \
  --expected-source-identity b58f48b5c19459c1273f3f4edf3fb67bd6f5e0e4c4d1c501218bf01b04ce6092 \
  --out ontolib/tests/decomposition/golden/neoplasm-highest-fanout.json
```

Generate the current full-corpus baseline from the fixed already-published cd4b run without
repeating concept work:

```bash
pdm run agent-replay generate-current-corpus-baseline neoplasm-cd4b7894-ce26-4a37-8d02-79f362099016
```

The tracked baseline binds run `neoplasm-cd4b7894-ce26-4a37-8d02-79f362099016`, all 15,633
worklist concepts, 14,884 decomposed outcomes, 139 explicitly persisted unknown outcomes, and
representation identity `8ce4ca52ece0804d2fcffe1ca597d7c99137bd00d8a6eb8710fbe99d8c1947c2`
(`pdm run agent-test ontolib/tests/decomposition/test_corpus_baseline.py::test_tracked_current_corpus_baseline_binds_exact_persisted_counts -v`,
2026-09-12). This generates a baseline candidate only.

The long-running CLI reports exact worklist progress and residual-metric progress. Interrupted runs
must be resumed with `--resume <run-id>`; completed work items are fenced and are not reprocessed.

## Retired R101 two-run tooling

#341 removed the R101 review, conservation, and comparator modules, commands, current
report goldens, and review packet fixtures. Their accepted decisions remain in packaged
policy data. D74's unexplained-R101-loss blocker is explicitly `not-evaluated`, owned by
#417, until that issue installs its per-run replacement.

### Historical 2b39 mixed-chain inventory and corrected projection

The separately identified #267 inventory and corrected projection belong to the historical 2b39
era, not to the current 8fb→cd4b comparator pair. Their exact tracked source is
`neoplasm-r101-v5-2b39-historical-conservation.json.gz`: file SHA-256
`f3d4f2bc551db08d3f665e92c9199ec09d9d80417f09a0c24e47a21b3a2de30f`, report identity
`25ed41375bc633505031a1e69327c41ac02a76f3f0759f86c899357b4fd4d6ba`, and new run
`neoplasm-2b39c3fc-0ae8-4220-971b-20d861ada722` (`pdm run agent-test
ontolib/tests/decomposition/test_mixed_chain_inventory.py::test_historical_mixed_chain_inventory_binds_available_report_evidence
-v`, 2026-09-12). That immutable report has 39 structural additions; it is retained only to
reproduce this historical derivation, not as the current diagnostic (`pdm run
agent-test --full-store ontolib/tests/decomposition/test_mixed_chain_full_store.py::test_historical_inventory_generator_replays_from_exact_report
-v`, 2026-09-12).

Generate the historical inventory and projection from their exact tracked report and persisted
2b39 state, then record only the validated generated artifacts:

```bash
pdm run agent-replay generate-mixed-chain-inventory
pdm run agent-replay record-mixed-chain-inventory
pdm run agent-replay generate-mixed-chain-corrected-projection
pdm run agent-replay record-mixed-chain-corrected-projection
```

The projection is explicitly typed `corrected-projection-not-a-run`: it does not mutate, resume,
or replace any completed run or diagnostic. It records 39 structural removals, zero additions, 42
metadata-row changes, and 39 disposition changes; the metadata classification is 36
`needs_review` true→false transitions, six relationship-group changes, and no `most_specific`
transitions. Its projection identity remains
`9df530273eead6b10d4f78df875999076bf2a2fd974a5aa2718f2ebe87c522a6` (`pdm run agent-test
--full-store ontolib/tests/decomposition/test_mixed_chain_full_store.py::test_corrected_projection_generator_binds_exact_historical_report
-v`, 2026-09-12). A corrected published run still requires database-backed admission before
execution; this projection preserves evidence and grants no content or publication authority.
