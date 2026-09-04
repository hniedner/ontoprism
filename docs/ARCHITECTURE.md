# Architecture

ONTOPRISM's current implementation is an NCIt-centered ontology storage, query, and
graph-visualization product whose distinctive output is a **decomposed
(non-pre-coordinated) NCIt**. It is built by lifting
the ontology vertical slice of the `fairdata` platform (see [DECISIONS.md](DECISIONS.md)
D1–D2) and adding a decomposition engine.

Its [target architecture](design/ontology-platform.md) is ontology-generic: a platform core
provides shared management capabilities, ontology adapters declare supported ontology-specific
capabilities, and domain policy controls semantic governance. No generic adapter type/system
currently exists. The implemented certified local repositories, curation, and
decomposition surfaces remain NCIt-centered (D86).

For plain-language definitions of decomposition, axes, fillers, genera, semantic types,
curated projections, source occurrences, partonomies, and relationship groups, see the
[shared terminology](../README.md#terminology). This document retains the precise ontology
terms after that introduction.

## Layout (keep-names)

```
ontoprism/
├── pyproject.toml            # root PDM project (distribution=false), tool config, test scripts
├── conftest.py               # puts ontolib/src & backend/src on sys.path (see DECISIONS D6)
├── docker-compose.yml        # Local Compose services published on :5433/:7888/:7889
├── Makefile  .env.example
├── .github/workflows/       # ci (path-filtered: quality/backend/coverage/web/integration
│                            #   + ci-summary) + release + security (codeql default setup,
│                            #   dependency-review, scorecard/OpenSSF) + update-readme + pr-title
├── ontolib/                  # LIFTED library — import name `ontolib`
│   ├── pyproject.toml        #   editable package (src layout)
│   ├── src/ontolib/
│   │   ├── terminologies/    #   SPARQL transport + NCIt/Uberon index builders/readers
│   │   ├── repositories/     #   cadsr read model
│   │   ├── core/  common/    #   shared primitives
│   │   └── decomposition/    #   NEW — non-pre-coordinated NCIt engine (M5)
│   └── tests/
├── backend/                  # LIFTED FastAPI app — import name `backend`
│   ├── pyproject.toml
│   ├── src/backend/
│   │   ├── main.py           #   app factory + /health (M0)
│   │   └── api/…/routers/    #   repo/graph/search/refresh + decomp (M6)
│   └── tests/
├── frontend/                 # SvelteKit 5 SSR/Node BFF + browser UI
└── docs/  ARCHITECTURE.md  DECISIONS.md  DATA_SETUP.md  design/  postcoordination-literature-review.md
```

## Data planes

For enhanced NCIt, these are distinct views rather than one blended identity. The official
release source plane and every OntoPrism overlay or derived view require separate identities.
Graph/storage separation currently protects the official stated plane from overlay mutation.
Independent source-view selection and recoverability for every enhanced release are target
guarantees, not current certification; byte recovery additionally requires retaining the original
artifact and digest. In the target data plane, an effective correction preserves the official source
plane and creates a named effective view by subtracting suppressed source axioms before
reasoning. Each suppressed canonical axiom remains retrievable in the immutable official source
and is shown in a nonempty `removed-from-effective` delta rather than deleted, absent, not-found,
or empty. Source containment means only immutable official-source preservation; the effective
enhanced view intentionally need not contain every official assertion.

- **QLever (immutable publisher-ontology indexes; D65)** — publisher-inferred NCIt in
  the default graph, publisher-stated NCIt in its protected named graph, and Uberon/CL
  in a separate default-graph index. Runtime reasoning is disabled. The local Compose stack
  unconditionally publishes the digest-pinned QLever index/server pair on :7888/:7889.
  Separately, when the `ontoprism-podman` Docker context is selected, the optional context
  chooses Podman as the container engine; it does not choose or alter those ports. The first-install
  bootstrap and #148's journaled, rollback-capable replacement of an existing NCIt index
  are implemented.
- **PostgreSQL (selected mutable authority)** — concept metadata/FTS cache, decomposition run
  state, provenance (`decomp_run`, `decomp_constituent`, `minted_concept`), source-bound
  NCIt search/embedding publication manifests, caDSR embeddings, and, when the curation
  API lands, the authoritative
  identity/revision/evidence/lifecycle/RDF projection for proposed NCIt content.
  Graph-explorer curation reads will compose this overlay with the identified QLever base
  so a committed edit does not wait for index rebuilding.
- **One SPARQL implementation** — QLever passed the seven real Uberon/NCIt data-shape
  contracts unchanged (`env UBERON_SPARQL_URL=http://127.0.0.1:7889
  NCIT_SPARQL_URL=http://127.0.0.1:7888 pdm run pytest
  ontolib/tests/repositories/xref/test_upstream_data_contract.py -m 'integration and
  full_store' -v`, 2026-08-10). The runtime dependency contract rejects any active
  Oxigraph package/import/service (`pdm run pytest
  backend/tests/test_supply_chain_contract.py::test_active_runtime_has_no_oxigraph_dependency
  -q`, 2026-08-10). Decomposition RDF and proposal RDF remain additive projections,
  never source graph mutations.

## Python model boundary

ONTOPRISM uses both dataclasses and Pydantic deliberately; they are not interchangeable.

- **Frozen dataclasses are domain values.** Pure algorithms exchange immutable facts,
  evidence, verdicts, and calculation results as dataclasses. Domain modules do not
  inherit from Pydantic models and do not embed Pydantic configuration or wire objects.
- **Pydantic models are boundary documents.** Configuration, API DTOs, persisted JSON,
  CLI-generated artifacts, manifests, and database/report payloads use strict Pydantic
  models because they validate untrusted or serialized shapes and provide a defined wire
  representation.
- **Boundary conversions are explicit.** A boundary model may not contain a domain dataclass as
  a field, and a domain dataclass may not contain a Pydantic model. Adapter functions map
  every field between the two representations. This keeps serialization concerns out of
  domain logic and prevents Pydantic's coercion/serialization behavior from becoming an
  implicit domain contract.
- **Ordinary service/client classes remain ordinary classes.** A QLever reader, cache,
  repository, or orchestrator is behavior, not a value schema; it uses neither dataclass
  nor `BaseModel` merely for convenience.

## Web request architecture

```text
browser → SvelteKit adapter-node (routing, SSR, hydration, same-origin /api BFF)
        → FastAPI (typed domain API) → QLever / PostgreSQL / certified caDSR
```

SvelteKit server loads own route-critical reads, so list, search, and detail content is
in the first HTML response. Browser calls remain same-origin under `/api`; the server-only
BFF reads `ONTOPRISM_FASTAPI_ORIGIN` and applies a bounded
`ONTOPRISM_FASTAPI_TIMEOUT_MS`. Neither value is delivered to browser code. FastAPI
remains the sole owner of domain endpoints and all QLever/Postgres/caDSR access.
The BFF removes caller-supplied forwarding and hop-by-hop headers, never follows an
upstream redirect, and completes each bounded FastAPI response body (at most 32 MiB)
before publishing its status and content to the browser. This prevents a loopback-trusted
client-address header from bypassing FastAPI rate limits and prevents redirects or
late-stalling bodies from escaping the gateway contract. It replaces the removed address
with SvelteKit's `getClientAddress()` value, which is socket-derived unless adapter-node
is explicitly configured to trust an operator-selected `ADDRESS_HEADER` and `XFF_DEPTH`.
It removes browser cookies from every proxied request. For protected ICD-O paths it also
removes caller entitlement headers, then injects the private runtime
`ICDO_ENTITLEMENT_KEY`. FastAPI independently
checks that consumer key, while `ENABLE_LICENSED_MAPPINGS` remains a separate capability
gate for ICD-O-bearing NCIt responses (`cd frontend && npx vitest run
src/lib/server/fastapi.test.ts && cd .. && pdm run pytest
backend/tests/test_icdo_api.py backend/tests/test_mappings_api.py -q`, 2026-08-14).

In development, Vite and the built Node server exercise the same SvelteKit `/api` route;
there is no Vite-only proxy path. The supported process wrapper loads the repository
`.env`. Production `node build` receives the private origin from its process environment
(or Node's `--env-file`); `.env` is not loaded implicitly by adapter-node. If adapter-node
is behind a trusted reverse proxy, configure `ORIGIN` directly or set
`PROTOCOL_HEADER`/`HOST_HEADER` only for headers overwritten by that trusted proxy.

The application exposes no caller-supplied raw SPARQL route: typed endpoints construct
the supported store queries. Re-enabling raw execution requires a separately reviewed
executor with proven store-side cancellation and resource bounds (D44).

Repository readiness is a certification boundary, not a ping. NCIt binds the exact
active candidate manifest and completed activation journal to a live same-release graph
observation; caDSR binds its persisted archive record to canonical row count and
fingerprint. Search and similarity readers accept derived publications only when their
persisted `source_identity` matches that certified active proxy (D68).

## Key inherited mechanism: NCIt roles are OWL restrictions

NCIt encodes pre-coordination as relationship requirements (OWL existential restrictions)
(`?c rdfs:subClassOf [ owl:onProperty ?R ; owl:someValuesFrom ?filler ]`), **not** as
direct triples (0 direct R-triples in the store; associations are direct A-triples). The
restriction-traversal query (`ontolib` `terminologies/ncit/graph_store_role_queries.py`)
is the backbone that makes roles queryable, and the foundation the decomposition engine
builds on. Porting it faithfully is the keystone of M1/M2 ("roles must render").

## Decomposition model (additive; exact reversibility quarantined)

Decomposition exposes a complex concept's semantic parts without replacing it. Legacy
pre-coordinated concepts are therefore **flagged, never deleted**
(`representationStatus="legacy-precoordinated"`) and linked to constituents via
`hasConstituent[axis, filler]`. Constituents come roles-first (100% already exist as
active concepts) with NLP/label parsing as fallback for label-only axes (laterality,
with/without <finding>, staging-manual version). The `neoplasm` (`C3262`) and `disease`
(`C2991`) populations are strict descendant closures of the stated named-class DAG,
including defined-class genus edges; disease contains neoplasm. Both use the same
axis-qualified algorithm and its semantic-type applicability gate. Regimen is reserved
for a distinct component-bag algorithm; gene/protein role families remain excluded.
Extraction runs off the **stated** OWL (DECISIONS D4/D51); the inferred store is used
only for validation/closure.

The human-facing view is a deliberately **lossy curated projection**. The representation
of record separately preserves the complete stated multi-parent definition DAG, every
genus/restriction group, and stable trace links from role- and parent-derived projected
constituents; minted NLP fallback retains separate proposal provenance (D50).
PostgreSQL and the additive RDF artifact both round-trip groups, review flags, and those
facts. The graph still never emits `owl:equivalentClass`; requests for equivalence fail
closed and new runs record no round-trip-fidelity value until a separate proof/validation
step establishes exact semantics (D43/D50). Most-specific collapse applies only to the
projection and never changes the complete record.

`op:PrimarySite` is anatomy-valued and cardinality `0..1` after class-projection review;
the pending projection may retain multiple review-required candidates rather than choose
silently. A future cancer-disease occurrence is likewise `0..1`. Absence in the resolved
class projection is not CUP: site-agnostic morphology classes simply do not pose the
question. The future
occurrence model must instead carry exactly one `primarySiteStatus` (`known`,
`unknown-cup`, `undetermined`, or `not-applicable`), with `known` iff a primary-site filler
is present. Patients may have any number of disease occurrences; occurrence individuation
(second primary versus metastasis) is upstream of this constraint and must never be
inferred from the site cardinality. D58 defines a future class-status derivation from the
site plus explicit complete-record unknown-primary evidence; the current projection does
not compute or persist it. The proposed no-site/no-CUP `not-applicable` summary is
class-local and never inherited by an occurrence:
a solid-tumour occurrence without established site evidence is `undetermined` (D58).
The future occurrence schema and API are tracked in #263.

The pending M1 reference candidate includes audited R103 normal-tissue-origin and R108
clinical-finding expectations after same-axis specificity and the versioned
`contracted-role-generic-v2` suppression list. R103 expectations carry their source-derived
non-defining modality; `ncit-26.07d-unsupported-filler-v1` excludes the two C54105 source
conflicts from accuracy content. Ten R104 CellOrigin and one R107
CytogeneticAbnormality survivor remain named scope omissions pending axis-level
adjudication; their complete-definition facts are still preserved. Production projection
recall remains depth-bounded. Is-a may collapse a broader filler on routed axes except
lineage classification, while R82 part-of may collapse only location-axis fillers.
Independent lineage classifiers remain ungrouped and uncollapsed (D59).

See the [design docs](design/) — the [decomposition assessment](design/ncit-decomposition-assessment.md)
(the *why* + verified prevalence numbers) and the [engine design](design/ncit-decomposition-engine.md)
(the *how*) — for the full rationale.
