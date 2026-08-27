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

Current readiness reads only the promoted state's embedded `.packet` for its existing
source, candidate-manifest, proposal-registry, count, and packet-identity semantics.
Before that packet is used, `load_r103_promoted_review_state` strictly validates the
entire promoted state, including its registry and dry-run. Consequently, a changed or
malformed decision vector fails readiness closed. Readiness does not copy or interpret
registry decisions or dry-run fields after the strict container load.
