# Evidence destinations

`docs/evidence/` contains tracked explanatory prose about evidence boundaries,
destinations and decision rationales. It is documentation, not a store for
non-documentary evidence. The
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
the active schema 4 packet or replayed through the active importer. Current schema-4
review artifacts are produced only through the immutable candidate chain. Generate the
independent ignored axis diagnostics with:

```bash
pdm run python scripts/adjudication.py generate-axis-diagnostics C35501 C12431 MINT-781c8c8c6096
```

The historical record contains 11 corrections and 4 escalations. Those dispositions are
context for the new blank review rather than active schema-4 decisions; they remain open
and block #274 and #127. Publication remains unauthorized.

## Preserved R103 history

The one-off R103 review chain was retired in #418. Its original review state,
terminal revision, and corroboration files remain byte-identical historical inputs to
the proposal-registry migration envelope; no active readiness or replay path consumes
the removed derived artifacts.
