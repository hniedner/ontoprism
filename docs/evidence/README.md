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

For the ontology-platform target (D86), evidence must also identify the view it supports.
Official/NCI-authored/`accepted-in-ncit` claims require evidence in an identified certified
official NCIt release; local approval or publication is not that evidence. Mapping and AI
evidence bind source, tool/model where applicable, and endpoint release identities rather than
mutable URLs or unqualified confidence.

## Group-review rationale admission (#274)

`evidence/group-review-packet-26.07d-schema3.json` is the byte-frozen historical schema-3
packet that binds `evidence/group-review-rationale-26.07d.md` and its compact JSON
sidecar. The Markdown is the authoritative contextual human record for all 18 review
rows. The sidecar stores digests and operational bindings only; it does not duplicate
rationale, questions, or context. Reviewer, date, outcomes, and rationales are local-SME
evidence, not independently validated source or standards claims, NCI acceptance,
publication, or publication authorization.

Schema 3 did not distinguish scoreable release-bound pairs from review-bearing emitted
pairs. It is retained only to interpret the historical review and is not converted into
the active schema 4 packet or replayed through the active importer. Generate fresh
ignored schema-4 diagnostics, review packet, blank workbook, pair-relation audit, and
blank validation with:

```bash
pdm run agent-replay generate-axis-diagnostics C35501 C12431 MINT-781c8c8c6096
pdm run agent-replay generate-group-review-rev2
```

The historical record contains 11 corrections and 4 escalations. Those dispositions are
context for the new blank review rather than active schema-4 decisions; they remain open
and block #274 and #127. Publication remains unauthorized.

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
