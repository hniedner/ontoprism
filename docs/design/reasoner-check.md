# Stored-run reasoner inspection

`pdm run reasoner-check --run <completed-run> --workspace <new-directory>`
compares stated NCIt with additive SubClassOf existential restrictions. `--oracle`
checks the accepted SME-oracle cohort against a BOT-extracted stated module.
No ledger writes occur. Disjoint co-fillers are inspection-only: existential
witnesses need not coincide. Individually refuted assertion rows can be previewed
against existing ledger assessments; joint/inherited conflicts without individual
attribution remain explicitly listed as `unattributed_unsatisfiable`.
If ledger preview fails, the comparison still prints with `ledger_preview_error`
and the command exits nonzero; it never reports a successful preview instead.

The shared ROBOT launcher deliberately retains the existing `RELEASE_DELTA_`
environment names. For the inspected full NCIt profile use
`RELEASE_DELTA_ROBOT_HEAP_GIB=24`,
`RELEASE_DELTA_ROBOT_MAX_ELEMENT_DEPTH=512`, and
`RELEASE_DELTA_ROBOT_TIMEOUT_SECONDS=600`. Depth zero is refused. See
[release-delta configuration](release-delta-command.md) for the shared settings.

ELK is not a full-OWL consistency proof when the input is outside OWL 2 EL.
The report includes profile diagnostics and labels such results
`not-proved-outside-EL`. A zero finding does not create positive evidence.
When either classification reports unsatisfiable classes, this comparison has no
entailment set for that input: `new_subsumptions` and `lost_subsumptions` are
`null`, not empty lists. The timeout above bounds each ROBOT invocation, not the
whole comparison; individual assertion trials can extend total runtime.
