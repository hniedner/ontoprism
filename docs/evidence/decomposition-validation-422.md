# Decomposition validation cleanup (#422)

The runtime policy applies an approved grouping; it does not re-adjudicate the
historical over-merge/over-split diagnosis. The historical rationale is in
`evidence/group-review-rationale-26.07d.md` and the superseding cervical stage
decision in `docs/evidence/cervical-stage-grouping-355.md`.

The following checks/data remain deliberately:

- `policy_identity` validates the whole packaged policy and binds it to the run's
  explicit engine/rules identity (#356). The engine does not hash Python source.
- `row_identity` remains audit data inside that whole-policy digest, but is no
  longer re-derived for every row during loading.
- `source_evidence_identity` is consumed by normalized-group ID derivation, not
  merely a self-hash. Removing it changes exported group identities.
- Group identity derivation and paired label checks remain at policy, domain,
  read-DTO and database boundaries: malformed source policies and database rows
  are actual inputs, and a group without its label is an invalid display state.
- Block evidence unions remain because runtime genus-fact validation consumes
  block facts/availability, and the policy builder emits these fields. They must
  agree with the per-pair source evidence.
- Historical decision and basis fields remain in the existing policy format:
  the research promotion-bundle reader consumes them, and the whole-policy digest
  includes them. Removing them needs a coordinated policy-data rebuild, not an
  ignored-field compatibility reader. Runtime historical diagnosis recomputation
  has been removed.
- The schema version and approved concept sets still reject the wrong policy
  generation/cohort; the redundant fixed row count and literal-only historical
  artifact metadata types have been removed.
- Content-derived fact/occurrence IDs are checked when reading stored definitions;
  keeping their cheap validation detects misassociated database records.

`Applied`/`NotApplicable` wrappers and the unused synthetic occurrence count have
been removed. The range-only projection cleanup (#357) already removed the
projection decision ledger. There is no `DiagnosticReductionPurpose` remaining
in the current code.
