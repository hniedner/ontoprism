# Decomposition golden-set candidates and adjudicated oracle

## Files in this directory

| File | What it is |
|---|---|
| `neoplasm-adjudicated.json` | **The M1 oracle.** `SME-ADJUDICATED`, NCIt 26.07d, 20 concepts, 154 expected pairs, reviewed by R. Hannes Niedner, MD. Produced by `import-workbook` from the attested #57 workbook. |
| `neoplasm-row-decisions.json` | **The reviewer's row-level decisions**, 189 rows from the same workbook — what the SME did with each engine suggestion, including the rows the oracle drops. Produced by `export-row-decisions`. |
| `neoplasm-engine-evidence.json` | The recorded engine run the oracle was scored against (`run_id` `neoplasm-3981f4d1…`). Tracked so the baseline is reproducible without a live store. |
| `neoplasm-corpus-comparison.json` | The #154 residual comparison: 13 denominator and 13 residual codes, being the engine evidence restricted to the 15 codes in `samples/ncit-26.07d-m1-review.json` (two of the 15 did not decompose). Its `evidence_identity` is computed **after** the payload, per D61. |
| `neoplasm.json`, `neoplasm-draft.json` | `AUTO-DRAFT` review inputs. **Not** oracles. Retained as seeds. |
| `proposal-registry.json` | The 7 mint/relation proposals bound to the oracle by `registry_identity`. |
| `complete-definition.json`, `minted-concepts.json` | Fixtures for the complete-definition and minting paths. |

Note a deliberate mismatch: the oracle's `_meta.corpus_evidence_identity` (`2be52e42…`) does
**not** equal the corpus file's `evidence_identity` (`2d64c0d6…`), and nothing compares them.
The first was pre-declared in the workbook before the artifact existed and can never be matched
by any run — that is the defect D61 records. The second was computed from the payload that
exists. The mismatch is expected and permanent.

## The M1 baseline this oracle records

Measured 2026-08-08 against the attested workbook, using the scoring code at `b4aa5e2`, and
recomputable from the tracked files above with no store and no workbook.

**Pair-level** — the oracle's expected pairs against the recorded engine run:

| | | asserted by `test_m1_baseline.py`? |
|---|---|---|
| precision / recall (`ncit_bound`, D59 strict denominator) | 0.7547 / 0.5229 | yes |
| engine pairs matching an expected pair (TP) | **80 of 106** | no — implied by precision |
| engine pairs that were wrong (FP) | **26** | no |
| expected pairs the engine never emitted (FN, `ncit_bound`: 153 − 80) | **73** | no |
| same, on the `augmented` view (154 − 80) | 74 | no |
| relationship-group agreement | **2 of 20 concepts** | yes |
| `residual_precoordination` — adjudication / #154 subset | 18/18 and 13/13, delta 0.0 | yes |

The rows marked "no" are derivable from the report but not pinned, so an engine change that
moved TP while holding the rounded ratios would not fail the suite.

**Row-level** — what the SME did with each of the 189 constituent decision rows:

| | `include` | `revise` | `exclude` | `not-needed` |
|---|---|---|---|---|
| `ENGINE SUGGESTION` (106 rows) | **48** | **42** | **16** | — |
| `ADD IF MISSING` (83 rows) | **63** | 1 | 4 | 15 |

**Read these two rates as different questions, not two views of one.** Each row records
the engine's own pair alongside the reviewer's, so both are checkable:

| | | |
|---|---|---|
| the engine proposed the right `(axis, filler)` | **80 of 106 = 0.7547** | `pair_preserved` — equals precision |
| the reviewer changed nothing at all about the row | **48 of 106 = 0.4528** | `included_rate` |

The gap is 32 rows on which the engine got the pair right and the reviewer still revised
something else — the relationship group, `needs_review`, or provenance. So "45%" is not
"the engine picked the wrong filler 55% of the time"; the filler was right 75% of the
time. It is the *rest* of the row the engine gets wrong, which is why #274
(relationship-group partition disagreeing on 18 of 20 concepts) is likely the larger
defect and #271 (compound fillers) the narrower one.

The `ENGINE SUGGESTION` / `not-needed` cell is **unrepresentable**, not merely unused: an
engine suggestion the reviewer never ruled on must not enter the denominator, so the model
rejects the combination and `cross_tab()` does not emit the cell.

The row-level and pair-level counts still will not reconcile line by line: a `revise` row
that changed the pair contributes to the never-emitted column, while one that changed only
the group does not. #275 made the row-level figures reproducible; before it, they existed
only in a gitignored workbook.

The `include` and `revise` rows are exactly the oracle's 154 expected pairs —
`test_m1_baseline.py` asserts that equality on `(code, axis, filler)`, that both files name
the same `workbook_identity`, and that the export's `ENGINE SUGGESTION` rows match the engine
run per concept. The equality must be keyed on the SME action, not on "the row names a pair":
3 of the 20 `exclude` rows still name the expectation the reviewer withdrew — the other 17
carry no pair at all — so filtering on filler presence yields 157 triples rather than 154.

Those three exclusions are worth reading, because they are evidence rather than noise:

```
C6135    op:AssociatedRegion  C12418   Head and Neck   (engine suggestion, excluded)
C101539  op:AssociatedRegion  C12418   Head and Neck   (candidate, excluded)
C4791    op:AssociatedRegion  C12727                   (candidate, excluded)
```

These are the same three concepts #267 names, and the engine proposed one of the three
itself. `C6135` is the clean case: the reviewer dropped `C12418` (Head and Neck) while
retaining `C13063` (Neck), which is exactly the outcome #267 says the ordering defect
prevents — raw `R101` fillers reduced for specificity *before* semantic routing can preserve
a broader region.

Do not generalise the rule from this paragraph. On `C4791` the excluded filler is `C12727`
(Heart) while the oracle *retains* `C12905` (Thoracic Cavity), which is broader than the code
excluded, alongside `C13004` (Endocardium). "Drop the broader region" therefore describes
`C6135` and not the cohort uniformly; anyone building a #267 regression fixture should read
all three rows rather than the summary.

Those numbers are the point of #57. They are why #271 (compound fillers), #267 (routing
order) and #274 (group partition) exist. Treat them as the baseline any engine change is
measured against — a change that moves them is expected to say so explicitly.

The #154 sample is a strict subset of this golden set, so the residual comparison cannot
detect divergence; the test that carries D37's intent runs against the full corpus in
#127 step 5 (D62).

## How adjudication works

The `AUTO-DRAFT` files are review input, not oracles. Correct extraction of stated
pre-coordination is **curation-heavy, not mechanical** — a
genus-chain walk over-collects and most-specific selection can pick the wrong filler
(engine design [§6.2](../../../../docs/design/ncit-decomposition-engine.md)).

The review loop is:

1. **Review** each automated suggestion against the source definition.
2. **Adjudicate** it as accepted, rejected, or revision-needed with SME identity,
   qualification, date, rationale, NCIt release, and certified source/run identity.
3. **Run** the candidate extractor against the certified source and bind its immutable
   run, detector, artifact, and evidence identities into the review packet.
4. After the artifact declares `SME-ADJUDICATED`, **score** accepted entries only; retain rejected/revision decisions in the review
   record and exclude `needs_review` pairs under D21.
5. **Iterate** without changing the oracle merely to match the engine.

The scoring scripts fail closed on `AUTO-DRAFT`; they cannot print oracle metrics until
the provenance-bearing adjudication contract is satisfied. This prevents automated
suggestions and engine output—both generated from implementation assumptions—from being
presented as human truth.

The final artifact requires:

- `_meta.schema_version = 3`, `_meta.status = "SME-ADJUDICATED"`, reviewer identity,
  qualification/date, NCIt version, and lowercase SHA-256 source, sample, run fingerprint,
  detector, engine-artifact, engine-evidence, corpus-evidence, proposal-registry, and
  workbook identities;
- a non-empty label and an `expected` object per accepted concept containing one typed outcome
  (`decomposed`, `residual`, `semantic-excluded`, or `atomic-no-op`), the complete
  semantic-type list, and unique constituent objects;
- an explicit relationship group (string or `null`), `needs_review` boolean, and
  reviewer-authored `provenance_status` on every constituent. Every non-NCIt provenance
  status also requires a `proposal_id` whose registry status and normalized axis match;
  NCIt-bound constituents require `proposal_id: null`; and
- per-concept adjudication status and rationale. Rejected/revision-needed entries must
  have `expected: null` and never enter metrics.

The final M1 artifact must contain 20–50 adjudicated concepts and include `C4791`,
`C35756`, and `C89995` or a recorded unsuitable-case decision. **#57 satisfied this on
2026-08-08; `neoplasm-adjudicated.json` is that artifact.** `neoplasm.json` remains a
starter seed.

The loader retains one complete expectation store for audit and outcome evaluation.
Reports derive provenance and modality views from each pair. `ncit_bound` filters the
expected set to pairs stamped for the artifact's NCIt release; all unprovenanced actual
engine emissions remain in its actual/false-positive denominator. `augmented` changes the
expected set by adding locally approved, submitted, or later-accepted proposal pairs.
`defining_only` and `non_defining` stratify
the NCIt-bound view from the versioned axis contracts. Merely proposed pairs enter neither metric.
Reviewer-flagged expected pairs remain visible as explicit exclusions and cannot silently
enter a metric. Engine `needs_review` values are pre-adjudication diagnostics; once a human
has resolved a pair, they do not override `Expected needs_review` or silently defer it. A
`decomposed` expectation must have at least one constituent; all other typed outcomes must
have none.

Primary-site occurrence status is deliberately not a constituent or pair metric. After
review, the class projection keeps anatomy-valued `op:PrimarySite` at `0..1`; pending
review may retain multiple candidates rather than choose silently. No site does not imply
unknown primary. D58 defines a future occurrence-level status vocabulary (`known`,
`unknown-cup`, `undetermined`, `not-applicable`) and a future class-level derivation from a
projected site plus explicit unknown-primary evidence. Neither is currently computed or
persisted, and status does not add an expected pair or change bound/augmented scores. A
future class-derived `not-applicable` value must never be inherited by an occurrence; a
solid-tumour occurrence without established site evidence is `undetermined` until
occurrence-level evidence resolves it.

The pending v14 candidate is designed to add audited R103 and R108 expectations after
same-axis collapse and `contracted-role-generic-v2` suppression. R104 and R107 are named scope omissions
pending axis adjudication, not silently deferred expectations. R82 partonomy contributes
to specificity only for location axes; lineage classifiers remain independent even under
is-a, while other routed axes may use is-a specificity. Group agreement compares complete expected and actual scorable
partitions, so a missing or extra pair also prevents a group match; it is not boolean
grouped/ungrouped parity.

Example final entry:

```json
{
  "code": "C6135",
  "label": "Reviewed concept",
  "expected": {
    "outcome": "decomposed",
    "semantic_types": ["Neoplastic Process"],
    "constituents": [
      {
        "axis": "op:StageValue",
        "filler": "C27970",
        "relationship_group": "stage",
        "needs_review": false,
        "provenance_status": "ncit-26.07d",
        "proposal_id": null
      }
    ]
  },
  "adjudication": {
    "status": "accepted",
    "rationale": "Reviewed against the stated NCIt definition."
  }
}
```

The artifact stores concepts as a JSON array so duplicate concept codes can be detected.
Use `pdm run adjudication import-workbook <workbook.xlsx> <proposal-registry.json>
<artifact.json>` to import a completed workbook and `pdm run adjudication evaluate
<artifact.json> <engine-evidence.json> <corpus-comparison.json> <proposal-registry.json>
<report.json>` to generate the canonical accepted-only report. Both commands fail closed
on pending review or identity drift.
Residual comparison inputs must list the exact denominator and residual concept codes;
historical aggregate counts alone are insufficient because they cannot prove membership.

`import-workbook` discards the `exclude` and `not-needed` rows — necessarily, since they
define no expectation — which is what made the acceptance rate unrecoverable from the
oracle. `export-row-decisions` keeps every row. It runs:

- the **workbook-level tamper gates** — sheet contract, sheet visibility, hidden reviewer
  rows and columns, formula cells, attestation, required evidence keys;
- the **`Concept Decisions` preconditions**, shared with the import so the two cannot
  diverge — required headers, no hidden concept row, no populated row with a blank code —
  followed by the orphan check, so a constituent row naming a concept the reviewer never
  declared, or declared only on a concealed row, cannot inflate the denominator;
- the **shared constituent row reader** — row identity, row type, SME action vocabulary,
  no `PENDING` engine suggestion, `Row Complete?` (waived for a `not-needed` row, which an
  engine suggestion can never be), and canonical `Expected Axis`/`Expected Filler`;
- the **row-shape gates** — an expected pair names both an axis and a filler or neither, a
  `not-needed` row names no expected pair, and `Engine Axis`/`Engine Filler` are present
  and canonical exactly on `ENGINE SUGGESTION` rows.

It does **not** run the kept-constituent gates — `Expected Provenance Status`,
`Expected needs_review`, `Expected Group`, `Expected Proposal ID` — nor the cohort or
proposal-registry gates; those belong to `import-workbook`. A workbook the export accepts
can still be rejected as an oracle, so run both.

The export carries a `payload_identity` — a SHA-256 over the whole payload, computed after
generation per D61 and checked at load — and `_meta` binds the rows to the run they measure
via `source_identity`, `run_id` and `engine_evidence_identity`. It is a self-consistency
digest stored inside the document it covers, so it detects an edit that does not recompute
it, **not** an edit that does; it is not a tamper seal. What closes the remaining gap is
that each row records the engine's own pair, so the recorded suggestions must equal the
emitted triples of `neoplasm-engine-evidence.json`. Together those turned four edits that
had moved the published rate to 53%, 40% and 38% with every test green into load failures.
`schema_version` is 4.

A row is one of three shapes, discriminated on `sme_action`, so the invariants are carried
by the type rather than by a validator: a **kept** row (`include`/`revise`) must name an
`expected` pair; an **excluded** row either names the expectation withdrawn or names
nothing — a half-named withdrawal is unrepresentable; an **unused candidate**
(`not-needed`) is `ADD IF MISSING` only and carries no expected pair. Every row also
carries `engine`, non-null exactly when `row_type` is `ENGINE SUGGESTION`, enforced as a
biconditional. The combination `ENGINE SUGGESTION` + `not-needed` has no inhabitant — an
engine suggestion the reviewer never ruled on cannot reach the denominator.

`cross_tab()` returns a typed result, not a grid of dictionaries. `included_rate` computes
0.4528 and `pair_preserved` counts the 80 kept rows whose expected pair is the engine's
own. Before this, 45% was arithmetic no code performed.

The tracked export was produced by:

```
pdm run adjudication export-row-decisions \
  <attested-workbook.xlsx> \
  ontolib/tests/decomposition/golden/neoplasm-row-decisions.json
```

`<attested-workbook.xlsx>` is the v14 SME adjudication workbook, sha256
`c1fbcb0d3d09c4846b519bc9…` — the same file `_meta.workbook_identity` names in both this
export and `neoplasm-adjudicated.json`, so the right input identifies itself rather than
depending on a path. The workbook is deliberately not tracked and is *not* required to
check any figure here: `test_m1_baseline.py` recomputes every count from the tracked
export alone.
