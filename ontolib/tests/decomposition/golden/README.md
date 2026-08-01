# Decomposition golden-set candidates and adjudicated oracle

The current `neoplasm.json` is an `AUTO-DRAFT` review input, not an oracle. Correct
extraction of stated pre-coordination is **curation-heavy, not mechanical** — a
genus-chain walk over-collects and most-specific selection can pick the wrong filler
(engine design [§6.2](../../../../docs/design/ncit-decomposition-engine.md)).

The review loop is:

1. **Review** each automated suggestion against the source definition.
2. **Adjudicate** it as accepted, rejected, or revision-needed with SME identity,
   qualification, date, rationale, NCIt release, and certified source/run identity.
3. Only after the artifact declares `SME-ADJUDICATED`, **run** a candidate extractor
   against the same source.
4. **Score** accepted entries only; retain rejected/revision decisions in the review
   record and exclude `needs_review` pairs under D21.
5. **Iterate** without changing the oracle merely to match the engine.

The scoring scripts fail closed on `AUTO-DRAFT`; they cannot print oracle metrics until
the provenance-bearing adjudication contract is satisfied. This prevents automated
suggestions and engine output—both generated from implementation assumptions—from being
presented as human truth.

The final artifact requires:

- `_meta.schema_version = 2`, `_meta.status = "SME-ADJUDICATED"`, reviewer identity,
  qualification/date, NCIt version, and lowercase SHA-256 source, sample, run,
  engine-artifact, and workbook identities;
- a non-empty label and an `expected` object per accepted concept containing one typed outcome
  (`decomposed`, `residual`, `semantic-excluded`, or `atomic-no-op`), the complete
  semantic-type list, and unique constituent objects;
- an explicit relationship group (string or `null`) and `needs_review` boolean on every
  constituent; and
- per-concept adjudication status and rationale. Rejected/revision-needed entries must
  have `expected: null` and never enter metrics.

The M1 cohort contains 20–50 adjudicated concepts and includes `C4791`, `C35756`, and
`C89995` or a recorded unsuitable-case decision. `neoplasm.json` remains a starter seed
until #57 records genuine SME adjudication.

The loader retains the complete expectation for audit and outcome evaluation. Existing
pair scorers receive only accepted constituents whose `needs_review` value is `false`;
review-flagged pairs remain visible as explicit exclusions and cannot silently enter a
metric. A `decomposed` expectation must have at least one constituent; all other typed
outcomes must have none.

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
        "needs_review": false
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
Use `pdm run adjudication import-workbook <workbook.xlsx> <artifact.json>` to import a
completed workbook and `pdm run adjudication evaluate <artifact.json>
<engine-evidence.json> <corpus-comparison.json> <report.json>` to generate the canonical
accepted-only report. Both commands fail closed on pending review or identity drift.
Residual comparison inputs must list the exact denominator and residual concept codes;
historical aggregate counts alone are insufficient because they cannot prove membership.
