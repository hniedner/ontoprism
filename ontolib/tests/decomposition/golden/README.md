# Decomposition golden-set candidates and adjudicated oracle

The current `neoplasm.json` is an `AUTO-DRAFT` review input, not an oracle. Correct
extraction of stated pre-coordination is **curation-heavy, not mechanical** — a
genus-chain walk over-collects and most-specific selection can pick the wrong filler
(engine design [§6.2](../../../../docs/design/ncit-decomposition-engine.md)).

The review loop is:

1. **Review** each automated suggestion against the source definition.
2. **Adjudicate** it as accepted, rejected, or revision-needed with SME identity, date,
   rationale, NCIt release, and certified source identity.
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

- `_meta.schema_version = 1`, `_meta.status = "SME-ADJUDICATED"`, NCIt version, and
  lowercase SHA-256 source identity;
- a non-empty label and unique `[axis, filler]` pairs per concept; and
- per-concept adjudication status, SME identity, ISO review date, and rationale.

The M1 cohort contains 20–50 adjudicated concepts and includes `C4791`, `C35756`, and
`C89995` or a recorded unsuitable-case decision. `neoplasm.json` remains a starter seed
until #57 records genuine SME adjudication.

Format: `constituents` is a list of `[axis, filler]` pairs — `axis` is an NCIt role code
(`R101`) or an `op:` axis (`op:Morphology`); `filler` is an NCIt concept code.
