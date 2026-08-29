# Evidence destinations

`docs/evidence/` contains tracked explanatory prose about evidence boundaries and
destinations. It is documentation, not a store for non-documentary evidence. The
top-level `evidence/` directory now has one deliberately narrow admission governed by
its exact inventory test and group-review loader, not by a generic pre-commit policy.

Existing tracked test goldens remain under
`ontolib/tests/decomposition/golden/` according to D63. A file under `tmp/` does not
become tracked merely because it is an evidence candidate.

The following categories are explicitly out of scope and are not admitted to the
top-level destination:

- XLSX files;
- TTL, NT, or OWL corpora;
- logs, dumps, and HEAD-bound readiness or verify captures;
- Podman artifacts, bakeoff artifacts, tools, or caches;
- configured corpora;
- licensed or restricted sources;
- secrets or local credentials;
- publisher PDFs; and
- large generated artifacts.

Everything OntoPrism emits is NCIt. Describe provenance using **derived from**,
**aligned to**, **corroborated by**, or **proposed, evidenced by** language; do not
describe emitted NCIt content as externally owned.

## Group-review rationale admission (#274)

`evidence/group-review-rationale-26.07d.md` is the byte-frozen, authoritative contextual
human record for all 18 review rows. Its JSON sidecar stores digests and operational
bindings only; it does not duplicate rationale, questions, or context. Reviewer, date,
outcomes, and rationales are local-SME evidence, not independently validated source or
standards claims, NCI acceptance, publication, or publication authorization.

Generate the ignored reviewed workbook and registry through the existing safe workbook
writer/importer, and produce the write-free dry run, with:

```bash
pdm run adjudication transcribe-group-review-evidence --packet tmp/m1-6-group-review-packet.json --markdown evidence/group-review-rationale-26.07d.md --sidecar evidence/group-review-rationale-26.07d.json --reviewed-xlsx tmp/m1-6-group-review-workbook-reviewed.xlsx --registry-output tmp/m1-6-group-review-decisions.json --dry-run-output tmp/m1-6-group-review-dry-run.json
```

All 18 rows are reviewed, but 11 corrections and 4 escalations remain open and block
#274 and #127. Readiness regeneration must wait until those open dispositions are
resolved; group review remains pending and publication remains unauthorized.

## R103 staging and readiness

`pdm run agent-replay generate-r103-review` remains the upstream staging producer
used for historical promotion and reproduction. Its packet remains meaningful even
though current readiness no longer consumes the standalone `tmp/` packet. The
producer is retained because the historical generation/promotion path still begins
with that source-bound packet and blank review workbook.

Current readiness reads the fixed
`ontolib/tests/decomposition/golden/r103-review-state-26.07d-rev2.json` path. The strict
revision loader validates the predecessor, embedded packet, effective registry, zero-unresolved
dry run, one exact exclusion preview, transcription provenance, qualification, and all identities.
Readiness binds the effective registry and C3264 decision identities in the satisfied R103
requirement. It does not bind that requirement to Git HEAD or a generated readiness-report
identity. Overall authorization remains false and publication remains unattempted because the
other human requirements are independent.

`r103-c3264-corroboration-26.07d.json` is a compact sidecar consumed by a strict loader against
the rev2 C3264 decision identity. It records citation metadata as corroboration, not proof, and
retains the exact non-propagating descendant qualification. It does not cache publisher content.
