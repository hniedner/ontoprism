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

Current readiness reads the preserved terminal revision together with the generated source
inventory, C12950 candidate enumeration, normalized authority, normalized corroboration, and
applied-policy report under `ontolib/tests/decomposition/golden/`. It binds all five current
artifact identities and leaves one R103 human selection pending for C2860. Overall authorization
remains false and publication remains unattempted.

`r103-c3264-corroboration-26.07d.json` is preserved historical input. Because no digest-bound
PubMed response bytes were retained, its successor
`r103-corroboration-normalized-26.07d.json` classifies the five citations as reviewer-supplied
references with `upstream_verified=false`; neither ESummary authority nor a verified date is
claimed. Both remain corroboration, not proof.

Run `pdm run agent-replay generate-r103-evidence-application` against the certified stated QLever
graph and RDF/XML artifact. The generator performs bounded QLever count/page reads, independent
streaming RDF/XML scans, canonical parity checks, strict historical/migration joins, and atomic
deterministic writes. It creates no proposal and makes no candidate-selection verdict.
