# Decomposition golden-set candidates and adjudicated oracle

The current `neoplasm.json` is an `AUTO-DRAFT` review input, not an oracle. Correct
extraction of stated pre-coordination is **curation-heavy, not mechanical** — a
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

The M1 cohort contains 20–50 adjudicated concepts and includes `C4791`, `C35756`, and
`C89995` or a recorded unsuitable-case decision. `neoplasm.json` remains a starter seed
until #57 records genuine SME adjudication.

The loader retains one complete expectation store for audit and outcome evaluation.
Reports derive provenance and modality views from each pair. `ncit_bound` contains only pairs
stamped for the artifact's NCIt release, while `augmented` also contains locally approved,
submitted, or later-accepted proposal pairs. `defining_only` and `non_defining` stratify
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

The M1 expected store covers audited R103 and R108 source facts after same-axis collapse
and `contracted-role-generic-v2` suppression. R104 and R107 are named scope omissions
pending axis adjudication, not silently deferred expectations. R82 partonomy contributes
to specificity only for location axes; non-location classifiers remain independent unless
is-a subsumption applies. Group agreement compares complete expected and actual scorable
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
