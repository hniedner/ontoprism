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
| `proposal-registry.json` | The proposal registry bound to the oracle. |
| `complete-definition.json`, `minted-concepts.json` | Fixtures for the complete-definition and minting paths. |
| `neoplasm-current-engine-evidence.json` | Current-source 20-code replay evidence; never the historical attested run. |
| `neoplasm-current-comparison.json` | Current replay metrics, grouping diagnoses, and all 189 row classifications. |
| `neoplasm-current-corpus-baseline.json` | Current-source full-corpus pre-change counts and exact representation identity. |
| `neoplasm-highest-fanout.json` | Current-source highest-fanout concepts and fixed query budgets. |
| `neoplasm-r101-v3-depth7-corpus-baseline.json` | Immutable depth-7 v3 baseline bound to the recovered completed run. |
| `neoplasm-r101-v4-conservation.json.gz` | Deterministic gzip of the schema-3, occurrence-level v3→v4 mechanical ledger; not content authorization. |
| `r101-review-registry-v3-sme.json.gz` | Deterministic test golden of the complete proposed review registry; it is not runtime package data or publication authorization. |

The compressed review registry has schema 3/status `proposed`, identity
`358b42f8279c067fbd0543572073cd5f6887eea0dc74d148483328c02ceb6975`, and exactly
3,291 atomic rows partitioned into 3,288 `approved-non-exclusive-coverage` and three
`rejected-retain-broader` outcomes; all 2,800 disease-exception values are false
(`pdm run pytest ontolib/tests/decomposition/test_collapse_veto_policy.py::test_tracked_registry_golden_has_exact_authorized_accounting -q`,
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
`op:PrimarySite` Uterus (`C12316`), is absent from the recorded engine
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

The last row does not make Heart (`C12727`) the primary site. The adjudicated primary site for
Left Atrial Myxoma (`C4791`) is Left Atrium (`C12869`); Endocardium (`C13004`) is retained as
tissue evidence, not described as an anatomical region.

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

Run the exact current 20-code cohort. The completed replay generated the persisted run
`neoplasm-0b00326b-6a9f-424f-b074-d4f1f8a0304d` (`pdm run agent-replay decompose-current`,
2026-08-24):

```bash
pdm run agent-replay decompose-current
pdm run agent-replay generate-current-evidence neoplasm-0b00326b-6a9f-424f-b074-d4f1f8a0304d
```

The generator derives every identity from those inputs and the completed persisted run. It refuses
source, release, manifest, worklist, run, fingerprint, artifact, representation, detector, oracle,
row-decision, registry, or evidence drift before replacing either output.

Generate the current axis diagnostics for the three explicit residual-detector branches (detected,
not detected, and proposed filler absent from source), then the normalized-group machine packet and
blank SME workbook:

```bash
pdm run agent-replay generate-axis-diagnostics C35501 C12431 MINT-781c8c8c6096
pdm run agent-replay generate-group-review
```

The generated group workbook keeps all human fields blank. For pair-only rows, the SME must
complete both `Pair Decision` and `Decision` with the same closed value; for grouping rows,
`Decision` is required and an optional `Pair Decision` must agree. After the SME saves a reviewed
copy, import it and run the write-free impact preview with these exact commands:

```bash
pdm run adjudication import-group-review --packet tmp/m1-6-group-review-packet.json --reviewed-xlsx tmp/m1-6-group-review-workbook-reviewed.xlsx --output tmp/m1-6-group-review-decisions.json
pdm run adjudication dry-run-group-review --packet tmp/m1-6-group-review-packet.json --registry tmp/m1-6-group-review-decisions.json --output tmp/m1-6-group-review-dry-run.json
```

The evidence is intentionally asymmetric: actual normalized groups cite current stated-source
occurrences, while expected-side source evidence is unavailable. The expected grouping is the
historical oracle proposal, not a source-stated relationship group. The evidence sheets display
the exact source facts, groups, occurrences, anchors, depth/path, and transformation witnesses;
labels and definitions are marked unavailable where the bound current evidence artifact contains
no source text. Machine evidence is never reviewer rationale.

### R103 manual SME review boundary (#294)

Generate the source-bound packet and blank workbook offline from the pinned stated OWL
artifact and the existing proposal registry (no QLever or database connection):

```bash
pdm run agent-replay generate-r103-review
```

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

These commands write `tmp/m1-6-axis-diagnostics.json`,
`tmp/m1-6-group-review-packet.json`, and `tmp/m1-6-group-review-workbook.xlsx`; all are gitignored
diagnostic/review artifacts. The workbook leaves Decision, Rationale, Reviewer, and Date blank, so
generation records no SME adjudication (`pdm run agent-replay generate-group-review`, 2026-08-24).

The group-review rule-evidence audit is deliberately narrow:

| Rule evidence kind | Existing producer and exact fields consumed |
|---|---|
| co-assertion preservation | `generate_current_evidence()` → `CurrentConceptEvidence.all_source_occurrences` and `CurrentConstituent.source_occurrences`, retaining `source_group_id`, `source_fact_id`, and `occurrence_id` beside normalized output-group identities |
| routing | `generate_current_evidence()` → `CurrentConstituent.axis` plus each cited occurrence's `role_code`, `filler_code`, source fact, source group, and occurrence identity |
| specificity collapse | `build_r101_conservation_report()` → `LedgerOccurrence.retained_r82_target`, `r82_path`, and exact structural occurrence fields; only rows that join to current evidence are emitted |
| repeated pairs | `generate_current_evidence()` → the complete `CurrentConstituent.source_occurrences` set for one normalized axis/filler pair |
| reviewed regrouping | `validate_current_comparison()` → current/expected partitions and grouping diagnosis, joined to current source occurrences and output groups; the historical expected partition is explicitly labelled as lacking source citations |

The packet and workbook are generated from exactly the tracked current evidence, tracked current
comparison, and tracked R101 conservation report by the wrapper above; each input is checked before
execution by `run_agent_replay.py` (`pdm run agent-replay generate-group-review`, 2026-08-24).

Generate the exhaustive fanout observation against the configured current source:

```bash
pdm run python scripts/observe_decomposition_fanout.py \
  --endpoint http://localhost:7888 \
  --source-manifest data/qlever-ncit/.ontoprism-ncit-candidate.json \
  --expected-source-identity b58f48b5c19459c1273f3f4edf3fb67bd6f5e0e4c4d1c501218bf01b04ce6092 \
  --out ontolib/tests/decomposition/golden/neoplasm-highest-fanout.json
```

Generate the full-corpus pre-change baseline only after a complete file publication:

```bash
pdm run decompose \
  --source-manifest data/qlever-ncit/.ontoprism-ncit-candidate.json \
  --branch neoplasm \
  --out tmp/m1-6-current-full-corpus.ttl

pdm run adjudication generate-corpus-baseline \
  --source-manifest data/qlever-ncit/.ontoprism-ncit-candidate.json \
  --run-id <completed-current-full-corpus-run-id> \
  --artifact tmp/m1-6-current-full-corpus.ttl \
  --output ontolib/tests/decomposition/golden/neoplasm-current-corpus-baseline.json
```

The long-running CLI reports exact worklist progress and residual-metric progress. Interrupted runs
must be resumed with `--resume <run-id>`; completed work items are fenced and are not reprocessed.

## R101 v3-to-v4 occurrence ledger

Regenerate the compressed report from the immutable completed runs (change only `--output` for a
second determinism run):

```bash
pdm run adjudication generate-r101-conservation \
  --source-manifest data/qlever-ncit/.ontoprism-ncit-candidate.json \
  --baseline ontolib/tests/decomposition/golden/neoplasm-r101-v3-depth7-corpus-baseline.json \
  --run-id neoplasm-d6b0df5e-aa18-4aa7-b8bb-9f8bc36c850a \
  --new-run-id neoplasm-0e88b7c0-eba0-42e6-8836-fa10f2604f46 \
  --endpoint http://localhost:7888 \
  --output "$TMPDIR/neoplasm-r101-v4-conservation-regenerated-1.json.gz" \
  --pre-resume-proof-identity f3c321c38deb8478f7a1abfa5c1edb1ef9ac3daf793d0dfe8d1e758eb62d2018 \
  --resume-dry-run-identity 2f5a0530f72028353a32b050a7e7a06a1880d7bcfe1aad4bcacd902333e7bd98 \
  --mixed-cohort-identity dda9c71a8a777e451a08fe81e4e2bae799f85e5f2c4984a90e5d95d71784777a
```

The generated schema-3 report contains 43,414 source occurrences partitioned into 30,040
projected, 10,083 unchanged-unprojected, 3,291 covered by stated R82 evidence, and zero
unresolved rows; the R82 evidence is independently partitioned into 1,954 one-step and 1,337
closure-only paths, and the non-R101 delta is zero
(`pdm run python -c 'from pathlib import Path; from ontolib.decomposition.r101_conservation import load_r101_conservation_report; r=load_r101_conservation_report(Path("ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz")); print(r.counts.model_dump())'`,
2026-08-19). The observed budgets are three PostgreSQL queries, 177 QLever queries, batches of
at most eight candidate pairs, eight R82 hops, and twenty asserted-superclass hops
(`pdm run python -c 'from pathlib import Path; from ontolib.decomposition.r101_conservation import load_r101_conservation_report; r=load_r101_conservation_report(Path("ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz")); print(r.query_metrics.model_dump())'`,
2026-08-19).

The self-excluding canonical semantic `json_identity` is
`f29210eee8e6d071d17d19a170118c9ea9b82380f4a887adc99eed27f15309a0`, its lossless TSV
identity is `b4182dcc676d8e6ad57234757b774fcee37f857f4d9e593bb6e83200a0b6a73d`, and its report
identity is `706613077f7b6edb6d684be57840cb4105d595f2efdf6e9778e49e13614a50a6`
(`pdm run python -c 'from pathlib import Path; from ontolib.decomposition.r101_conservation import load_r101_conservation_report; r=load_r101_conservation_report(Path("ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz")); print(r.json_identity,r.tsv_identity,r.report_identity)'`,
2026-08-19). Two independent regenerations from the immutable completed runs produced byte-equal
gzip files
(`cmp -s ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz "$TMPDIR/neoplasm-r101-v4-conservation-regenerated-1.json.gz" && cmp -s "$TMPDIR/neoplasm-r101-v4-conservation-regenerated-1.json.gz" "$TMPDIR/neoplasm-r101-v4-conservation-regenerated-2.json.gz"`,
2026-08-19).

The tracked gzip is 3,926,797 bytes with file SHA-256
`bf5ea01f2213c09766e6affc188c056c7dee07e1742f0e80c7afc6a5ffb4c014`; its decompressed,
indented canonical JSON is 49,076,502 bytes with byte SHA-256
`14fa41a97f78b28844868eae8e48929e530ded52787d88d84c1ba822b7027862`
(`pdm run python -c 'import gzip,hashlib,pathlib; p=pathlib.Path("ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz"); raw=gzip.decompress(p.read_bytes()); print(p.stat().st_size,hashlib.sha256(p.read_bytes()).hexdigest(),len(raw),hashlib.sha256(raw).hexdigest())'`,
2026-08-19). Both differ deliberately from `json_identity`, which hashes semantic JSON after
excluding authorization, publication, and identity fields. `report_identity` covers the complete
report except itself. The 22,220,891-byte TSV is not tracked; generating it on demand from
decompressed occurrences produces SHA-256 `b4182dcc676d8e6ad57234757b774fcee37f857f4d9e593bb6e83200a0b6a73d`,
equal to `tsv_identity`
(`pdm run python -c 'import hashlib; from pathlib import Path; from ontolib.decomposition.r101_conservation import load_r101_conservation_report,r101_ledger_tsv_bytes; r=load_r101_conservation_report(Path("ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz")); t=r101_ledger_tsv_bytes(r); print(len(t),hashlib.sha256(t).hexdigest(),r.tsv_identity)'`, 2026-08-19).

The exact non-R101 delta evidence contains zero canonical rows and binds the old run, new run, and
the SQL query contract identity
`2ae560df8f11a233a77860458dc9a12b01b3ebf3f25b900afb369a69363bacf1`; the reported count is
derived from those rows
(`pdm run python -c 'from pathlib import Path; from ontolib.decomposition.r101_conservation import load_r101_conservation_report; e=load_r101_conservation_report(Path("ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz")).non_r101_delta_evidence; print(e.old_run_id,e.new_run_id,e.query_identity,len(e.rows))'`,
2026-08-19).

Mechanical validation is complete, content authorization is pending, and publication is blocked
(`pdm run python -c 'from pathlib import Path; from ontolib.decomposition.r101_conservation import load_r101_conservation_report; r=load_r101_conservation_report(Path("ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz")); print(r.mechanical_status,r.content_authorization.status,r.publication_gate)'`,
2026-08-19). No authorization is recorded here. SME pattern review is deferred to the final M1.6
milestone review; this ledger must not be described as published or accepted content.

### Prepare the #267 human review packet

The exact tracked inputs and configured read-only label endpoint are:

```bash
test -f ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz
test -f data/qlever-ncit/.ontoprism-ncit-candidate.json

pdm run adjudication prepare-r101-review-packet \
  --report ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz \
  --source-manifest data/qlever-ncit/.ontoprism-ncit-candidate.json \
  --endpoint http://localhost:7888 \
  --output-packet tmp/r101-review-packet-v3.json \
  --output-xlsx tmp/r101-review-workbook-v3.xlsx
```

Before labels are read, this command reuses the decomposition source-snapshot certification path:
nine bounded QLever checks require the live endpoint's exact NCIt source observation, source
identity, and ontology release to match the explicit candidate manifest and report. It then reads
release-bound labels for every disease, site, and path code in batches of at most 500. The real
packet used five label queries after nine source checks; a missing or multiple distinct label fails
closed (`pdm run test-integration-full-store -k r101_review_labels_match_real_qlever_in_bounded_batches`,
2026-08-20). The command refuses unless 3,291 covered occurrences reconcile to exactly 162
patterns and 2,800 disease propositions. The packet keeps the complete occurrence/source/group/
anchor/path audit identities, full report/source/run/TTL/proof bindings, readable labels and paths,
frozen propositions, five denial flags, and immutable disease-to-occurrence membership. Generated
packet and workbook paths are gitignored review artifacts, not tracked evidence.

The workbook is for people rather than audit joins. Its exact sheets are `Instructions and
Semantics`, `Pattern Review`, `Disease Propositions`, `Column Definitions`, `Review Examples`, and
veryHidden `Bindings`; there is no occurrence sheet. Reviewer-facing sheets contain no internal
pattern, row, occurrence, source-fact, group, anchor, path identity, hash, or raw JSON and do not
require reviewers to handle hashes. `Bindings` carries the exact SHA-256 packet, guidance,
visible-row, and membership identities plus schema and release for mechanical binding.
The formula-free workbook uses automatic calculation; sheet protection is an anti-accident aid,
not security. Import regenerates every immutable visible cell from the separate packet, so an
openpyxl/Excel container re-save is benign but a semantic cell edit refuses
(`pdm run pytest ontolib/tests/decomposition/test_r101_review.py -q`, 2026-08-20).

Read `Instructions and Semantics` first. Approval means only `non-exclusive projection coverage`
for frozen disease/source occurrences. It does not mean equivalence, universality, completeness,
exclusivity, every case occurs only at the retained site, or the retained site is the only valid
site. Source assertions remain preserved and multiple valid narrower sites remain independent.
Every disease row is generated with `Exception?=No` and a blank rationale. This scope default has
no effect until its pattern is approved: reviewers change only true exceptions to `Yes` and add a
disease-specific rationale. Missing or invalid exception values refuse import, and all rows for a
non-approve pattern must remain `No` with blank rationale. Pattern decision, rationale, reviewer,
and date cells remain blank; the generator creates no approval.
The supplied SEER/ICD-O pilot conclusion appears only as generic guidance: zero strict
rule-eligible cases means no automation and no safe workload reduction. The workbook has no SEER
decision columns (`pdm run python -c 'from openpyxl import load_workbook; b=load_workbook("tmp/r101-review-workbook-v3.xlsx"); print("\\n".join(str(c.value or "") for r in b["Instructions and Semantics"].iter_rows() for c in r)); print(tuple(c.value for c in b["Pattern Review"][1]))'`,
2026-08-20). The external pilot command/date/input hashes were not supplied in the workspace, so
the detailed pilot counts remain blocked from durable certification rather than being inferred.

The current packet identity is
`fa9cca72f60affedf20ff420423f5f30c1aeabcff1bc54d53b05a6a7b419fc59`; guidance,
visible-row, and membership identities are
`fc315ee3633585693bd6db22f193c83b23fec94ad35412cafb80d40898b4c39b`,
`97a1d3084e9f555887bf931424c707b33083fbe654b20a1ef417a0462e35f6f7`, and
`756943698475d2313d7c1c6802fb2e0055585f5ce005b1575a48d0f8aa8702dd`
(`pdm run python -c 'import json; from pathlib import Path; p=json.loads(Path("tmp/r101-review-packet-v3.json").read_text()); print({k:p[k] for k in ("packet_identity","guidance_identity","visible_rows_identity","membership_identity")})'`,
2026-08-20). Packet/workbook file SHA-256 values are
`82c865f0b25624c2b6e968b724383385b55748c393650714281c16eceee701dd` and
`8d8993cac4373f67a99022e3db60f917b82f0cb160444e7159d9ca32f8fb4a35`
(`shasum -a 256 tmp/r101-review-packet-v3.json tmp/r101-review-workbook-v3.xlsx`,
2026-08-20).

Do not fill the generated real workbook for preflight. Copy it to the clearly named retained path
`tmp/r101-review-workbook-v3-TEST-ONLY.xlsx` and fill all 162 pattern decisions with conspicuous
`TEST-ONLY` values. Keep the 2,800 generated disease exception defaults unchanged, then run:

```bash
pdm run adjudication import-r101-review-decisions \
  --packet tmp/r101-review-packet-v3.json \
  --reviewed-xlsx tmp/r101-review-workbook-v3-TEST-ONLY.xlsx \
  --output tmp/r101-review-registry-v3-TEST-ONLY.json \
  --provenance test-only

pdm run adjudication dry-run-r101-decision-expansion \
  --report ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz \
  --packet tmp/r101-review-packet-v3.json \
  --registry tmp/r101-review-registry-v3-TEST-ONLY.json \
  --output tmp/r101-review-preflight-v3-TEST-ONLY.json
```

Import expands each pattern decision to one proposed atomic decision per frozen occurrence. An
approved disease exception excludes all of that disease's pattern occurrences; reject records
retain-broader outcomes; individual review and abstention record follow-up/escalation rather than
approval. The TEST-ONLY all-approve/no-exception run produced 3,291 approval outcomes and a dry-run
verdict of `validated-proposed-registry` with `writes_performed=false`
(`pdm run python -c 'import json; from pathlib import Path; from collections import Counter; r=json.loads(Path("tmp/r101-review-registry-v3-TEST-ONLY.json").read_text()); d=json.loads(Path("tmp/r101-review-preflight-v3-TEST-ONLY.json").read_text()); print(len(r["atomic_decisions"]),Counter(x["outcome"] for x in r["atomic_decisions"]),d)'`,
2026-08-20). No report authorization or publication is created, and the real decision cells remain
blank until independent human review.

The deleted schema-2 module defines 30 AST-level test functions; the schema-3 module defines 27
AST-level test functions and pytest collects 39 cases after parametrization. Functions and
collected cases are different counts, so neither is described as a count of semantic contracts
(`git cat-file -e '1f44c91018b23dadf2d64adc2ae0b94e2a2ad231:ontolib/tests/decomposition/test_r101_conservation.py' && pdm run python -c 'import ast,subprocess,pathlib; old=subprocess.check_output(["git","show","1f44c91018b23dadf2d64adc2ae0b94e2a2ad231:ontolib/tests/decomposition/test_r101_conservation.py"],text=True); new=pathlib.Path("ontolib/tests/decomposition/test_r101_occurrence_ledger.py").read_text(); f=lambda s:[n.name for n in ast.walk(ast.parse(s)) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name.startswith("test_")]; print({"schema2_ast_functions":len(f(old)),"schema3_ast_functions":len(f(new))})' && pdm run pytest ontolib/tests/decomposition/test_r101_occurrence_ledger.py --collect-only -q`,
2026-08-19). The migration ledger is:

| Schema-2 contract family | Schema-3 replacement |
|---|---|
| detector/schema drift | semantic-validator detector mutation plus stale-detector refusal |
| total unique occurrence dispositions | exact structural-key inventory, duplicate, mismatched, or partial refusal, deterministic partition |
| projected, unchanged, R82-covered, unresolved | explicit four-way occurrence partition and publication refusal |
| R82 completeness and query bounds | replayable one-step/closure paths, loader-level reversal/disconnection/source/depth/axis refusal, real edge-removal test, bounded full-store C5356 and C5552 tie test |
| R101 versus non-R101 deltas | canonical PostgreSQL delta rows bound to exact query and run identities, with count derived from evidence |
| C4791 cohort behavior | generated C6135/C101539/C4791/C5356 sentinel contract |
| schema/count/payload/authorization corruption | strict schema-3 loader, identity, count, state, and digest validators |
| duplicate links and minted fillers | strict link models plus lossless minted old-link TSV roundtrip |
| baseline/run/fingerprint/release binding | generated report bindings to both completed runs, baseline, source, release, detector, and three continuation identities |
| atomic persistence | one gzip JSON file is atomically replaced after generated TSV digest and identity validation; TSV is not persisted |

The former progress heartbeat and semantic-lookup-double tests are not migrated: schema 3 consumes
persisted v3/v4 occurrence links in one bounded PostgreSQL query and does not perform the removed
per-concept semantic lookup loop. The focused schema-3 suite, disposable PostgreSQL/QLever
contracts, and configured full-store contract execute the replacements
(`pdm run pytest ontolib/tests/decomposition/test_r101_occurrence_ledger.py -q && pdm run python scripts/run_safe_integration.py ontolib/tests/decomposition/test_pre_resume_integration.py::test_r101_candidate_query_preserves_old_and_new_occurrence_origins ontolib/tests/decomposition/test_stated_integration.py::test_r82_paths_preserve_direct_edges_and_edge_removal_on_real_qlever -q && pdm run test-integration-full-store -k tied_highest_fanout_ledgers_and_paths_match_generated_report`,
2026-08-19).
