# Evidence destinations

`docs/evidence/` contains tracked explanatory prose about evidence boundaries and
destinations. It is documentation, not a store for non-documentary evidence.

A future top-level `evidence/` directory is intended for compact, durable,
identity-bound non-documentary evidence. That directory does not yet exist, and no
repository gate currently enforces admission to it. The first file proposed for that
destination must include a separately reviewed admission/policy test; this statement
does not make that future policy an enforced constraint today.

Existing tracked test goldens remain under
`ontolib/tests/decomposition/golden/` according to D63. A file under `tmp/` does not
become tracked merely because it is an evidence candidate.

The following categories are explicitly out of scope and are not admitted to the
future top-level destination:

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
