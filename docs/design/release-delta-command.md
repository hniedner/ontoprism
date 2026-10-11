# Release-delta command (#523)

`pdm run release-delta --from 26.07d --to 26.09d` prints JSON only after both
certified stores and their source artifacts pass before/after checks. No activation
or persisted report is performed. The inactive sibling is served only for the
comparison via `DockerQleverRuntime.observe`, then its owned server is removed.

Required environment settings (also read from `.env`):
- `RELEASE_DELTA_SOURCES`: JSON object keyed by release, each with `manifest` path.
- `RELEASE_DELTA_ACTIVE_ENDPOINT`: active source URL (normally loopback port 7888).
- `RELEASE_DELTA_HISTORY_RELEASES`: ordered JSON list, e.g. `["26.08e","26.09d"]`.
- `RELEASE_DELTA_ROBOT_HEAP_GIB`: positive integer; measured full-NCIt profile **24**.
- `RELEASE_DELTA_ROBOT_MAX_ELEMENT_DEPTH`: nonnegative integer; full profile **0**.
- `RELEASE_DELTA_ROBOT_TIMEOUT_SECONDS`: positive integer; allow for full-file work.
The default ROBOT launcher and application dependencies remain unchanged.

History files and READMEs come from each release's archive folder. Release-chain
gaps, duplicates, ordering and target mismatches fail; declared date windows are
provenance only. Repeated lifecycle events across files fail; modifies may repeat.
Reconciliation differences are reported, not refused: the measured pair has 1,050
history creates versus 1,049 new classes. **C3050 already exists in 26.07d and
26.09d**, labelled Gastrin-Producing Neuroendocrine Tumor, without P310 status.
Its September create/modify rows therefore do not mean a newly declared class;
the publisher's reason for that create event is not established.

CI uses fixtures. The local `full_store` contract and Neoplasm-scope demo remain
pending owner-run sibling certification at the exact approved manifest path;
this documentation is not evidence they ran. Never select a candidate by glob.
