# Decomposition golden-set candidates and adjudicated oracle

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
also kept. Its revised pair, `C206219 op:PrimarySite C12316`, is absent from the recorded engine
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

```
C6135    op:AssociatedRegion  C12418   (engine suggestion, excluded)
C101539  op:AssociatedRegion  C12418   (candidate, excluded)
C4791    op:AssociatedRegion  C12727   (candidate, excluded)
```

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
cohort codes `C4791`, `C35756`, and `C89995`
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

Required configured inputs:

```bash
test -f data/qlever-ncit/.ontoprism-ncit-candidate.json
test -f samples/ncit-26.07d-m1-current-replay.json
test -f ontolib/tests/decomposition/golden/neoplasm-adjudicated.json
test -f ontolib/tests/decomposition/golden/neoplasm-row-decisions.json
test -f ontolib/tests/decomposition/golden/proposal-registry.json
```

Run the exact current 20-code cohort, then generate both evidence outputs in one command:

```bash
pdm run decompose \
  --source-manifest data/qlever-ncit/.ontoprism-ncit-candidate.json \
  --branch neoplasm \
  --sample-manifest samples/ncit-26.07d-m1-current-replay.json \
  --out tmp/m1-6-current-replay.ttl

pdm run adjudication generate-current-evidence \
  --sample-manifest samples/ncit-26.07d-m1-current-replay.json \
  --oracle ontolib/tests/decomposition/golden/neoplasm-adjudicated.json \
  --row-decisions ontolib/tests/decomposition/golden/neoplasm-row-decisions.json \
  --proposal-registry ontolib/tests/decomposition/golden/proposal-registry.json \
  --run-id <completed-current-replay-run-id> \
  --artifact tmp/m1-6-current-replay.ttl \
  --engine-output ontolib/tests/decomposition/golden/neoplasm-current-engine-evidence.json \
  --comparison-output ontolib/tests/decomposition/golden/neoplasm-current-comparison.json
```

The generator derives every identity from those inputs and the completed persisted run. It refuses
source, release, manifest, worklist, run, fingerprint, artifact, representation, detector, oracle,
row-decision, registry, or evidence drift before replacing either output.

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
