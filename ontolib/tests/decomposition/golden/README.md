# Decomposition golden-set candidates and adjudicated oracle

## Files in this directory

| File | What it is |
|---|---|
| `neoplasm-adjudicated.json` | **The M1 oracle.** `SME-ADJUDICATED`, NCIt 26.07d, 20 concepts, 154 expected pairs, reviewed by R. Hannes Niedner, MD. Produced by `import-workbook` from the attested #57 workbook. |
| `neoplasm-row-decisions.json` | **The reviewer's row-level decisions**, 189 rows from the same workbook — what the SME did with each engine suggestion, including the rows the oracle drops. Produced by `export-row-decisions`. |
| `neoplasm-engine-evidence.json` | The recorded engine run the oracle was scored against (`run_id` `neoplasm-3981f4d1…`). Tracked so the baseline is reproducible without a live store. |
| `neoplasm-corpus-comparison.json` | The #154 15-code residual comparison, derived from the engine evidence restricted to `samples/ncit-26.07d-m1-review.json`. Its `evidence_identity` is computed **after** the payload, per D61. |
| `neoplasm.json`, `neoplasm-draft.json` | `AUTO-DRAFT` review inputs. **Not** oracles. Retained as seeds. |
| `proposal-registry.json` | The 7 mint/relation proposals bound to the oracle by `registry_identity`. |
| `complete-definition.json`, `minted-concepts.json` | Fixtures for the complete-definition and minting paths. |

## The M1 baseline this oracle records

Measured 2026-08-08 against the attested workbook and reproduced byte-identically at
`b4aa5e2`. Every figure below is asserted by `test_m1_baseline.py` and recomputable
from the tracked files above with no store and no workbook.

**Pair-level** — the oracle's expected pairs against the recorded engine run:

| | |
|---|---|
| precision / recall (`ncit_bound`, D59 strict denominator) | 0.7547 / 0.5229 |
| engine pairs matching an expected pair | **80 of 106** |
| engine pairs that were wrong | **26** |
| expected pairs the engine never emitted | **74** |
| relationship-group agreement | **2 of 20 concepts** |
| `residual_precoordination` — adjudication / #154 subset | 18/18 and 13/13, delta 0.0 |

**Row-level** — what the SME did with each of the 189 constituent decision rows:

| | `include` | `revise` | `exclude` | `not-needed` |
|---|---|---|---|---|
| `ENGINE SUGGESTION` (106 rows) | **48** (45%) | **42** | **16** | 0 |
| `ADD IF MISSING` (83 rows) | **63** | 1 | 4 | 15 |

The two provenances measure different things and will not reconcile directly: a
`revise` row replaces one pair with another, so it lands in both the wrong-pair and
never-emitted rows above. 80/106 and 48/106 answer different questions, and #275 made
the second one reproducible.

The `include` and `revise` rows are exactly the oracle's 154 expected pairs —
`test_m1_baseline.py` asserts that equality on `(code, axis, filler)`, and that both
files name the same `workbook_identity`, so neither can drift from the other or be
regenerated from a different review. The equality must be keyed on the SME action, not
on "the row names a pair": an `exclude` row necessarily names the pair it excludes, so
filtering on filler presence yields 157 triples rather than 154.

Those three exclusions are worth reading, because they are evidence rather than noise:

```
C6135    op:AssociatedRegion  C12418   Head and Neck   (engine suggestion, excluded)
C101539  op:AssociatedRegion  C12418   Head and Neck   (candidate, excluded)
C4791    op:AssociatedRegion  C12727                   (candidate, excluded)
```

These are the same three concepts #267 names. The reviewer excluded the *broader* region
where a narrower one was already routed to the same axis — `C6135` retained `C13063`
(Neck) and dropped `C12418` (Head and Neck). The oracle therefore corroborates #267's
ordering defect independently: raw `R101` fillers are reduced for specificity before
semantic routing, which can preserve a broader region. The engine proposed one of the
three itself.

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
oracle. `export-row-decisions` keeps every row. It runs the workbook-level tamper gates
(sheet contract, sheet visibility, hidden reviewer rows and columns, formula cells,
attestation, required evidence keys) and the shared constituent row reader (row identity,
row type, SME action vocabulary, no `PENDING` engine suggestion, `Row Complete?`, and
canonical `Expected Axis`/`Expected Filler`). It does **not** run the kept-constituent
gates — `Expected Provenance Status`, `Expected needs_review`, `Expected Group`,
`Expected Proposal ID` — nor any `Concept Decisions`, cohort or proposal-registry gate;
those belong to `import-workbook`. A workbook the export accepts can still be rejected as
an oracle, so run both. The tracked export was produced by:

```
pdm run adjudication export-row-decisions \
  tmp/plans/M1-57_SME_Adjudication_Workbook_FINAL-REVIEW-PENDING_v14.xlsx \
  ontolib/tests/decomposition/golden/neoplasm-row-decisions.json
```

Re-running it against the same workbook is byte-identical. The workbook is gitignored
and is *not* required to check the counts: `test_m1_baseline.py` recomputes them from
the tracked export.
