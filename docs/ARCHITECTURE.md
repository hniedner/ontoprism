# Architecture

ONTOPRISM is an ontology storage, query, and graph-visualization platform whose
distinctive output is a **decomposed (non-pre-coordinated) NCIt**. It is built by lifting
the ontology vertical slice of the `fairdata` platform (see [DECISIONS.md](DECISIONS.md)
D1–D2) and adding a decomposition engine.

## Layout (keep-names)

```
ontoprism/
├── pyproject.toml            # root PDM project (distribution=false), tool config, test scripts
├── conftest.py               # puts ontolib/src & backend/src on sys.path (see DECISIONS D6)
├── docker-compose.yml        # postgres(:5433) + oxigraph-ncit(:7888) + oxigraph-uberon(:7889)
├── Makefile  .env.example
├── .github/workflows/       # ci (path-filtered: quality/backend/coverage/web/integration
│                            #   + ci-summary) + release + security (codeql default setup,
│                            #   dependency-review, scorecard/OpenSSF) + update-readme + pr-title
├── ontolib/                  # LIFTED library — import name `ontolib`
│   ├── pyproject.toml        #   editable package (src layout)
│   ├── src/ontolib/
│   │   ├── storage/          #   SPARQL HTTP storage boundary, pyoxigraph compat
│   │   ├── terminologies/    #   ncit (graph store, role/restriction queries), uberon
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
├── frontend/                 # LIFTED SvelteKit 5 app (M4)
└── docs/  ARCHITECTURE.md  DECISIONS.md  DATA_SETUP.md  design/  postcoordination-literature-review.md
```

## Data planes

- **QLever (selected immutable ontology index; D65)** — publisher-inferred NCIt in
  the default graph, publisher-stated NCIt in its protected named graph, and Uberon/CL
  in a separate default-graph index. Runtime reasoning is disabled. #163 packages the
  pinned index/server pair and removes both Oxigraph services; #148 owns validated NCIt
  sibling activation. Until those land, the development compose file still starts the
  two incumbent Oxigraph services on :7888/:7889 as a migration state, not the target
  architecture.
- **PostgreSQL (selected mutable authority)** — concept metadata/FTS cache, decomposition run
  state, provenance (`decomp_run`, `decomp_constituent`, `minted_concept`), caDSR read
  tables, and, when the curation API lands, the authoritative
  identity/revision/evidence/lifecycle/RDF projection for proposed NCIt content.
  Graph-explorer curation reads will compose this overlay with the identified QLever base
  so a committed edit does not wait for index rebuilding.
- **No target Oxigraph plane** — QLever passed the seven real Uberon/NCIt data-shape
  contracts unchanged, so retaining a second SPARQL implementation has no demonstrated
  product benefit (`env UBERON_SPARQL_URL=http://127.0.0.1:7303 pdm run pytest
  ontolib/tests/repositories/xref/test_upstream_data_contract.py -m 'integration and
  full_store' -v`, 2026-08-09). The current Oxigraph containers are removed by #163.
  Decomposition RDF and proposal RDF remain additive projections, never source graph
  mutations.

The frontend talks only to the FastAPI backend; the backend owns all SPARQL/Postgres
access. The application exposes no caller-supplied raw SPARQL route: typed endpoints
construct the supported store queries. Re-enabling raw execution requires a separately
reviewed executor with proven store-side cancellation and resource bounds (D44).

## Key inherited mechanism: NCIt roles are OWL restrictions

NCIt encodes pre-coordination as OWL existential restrictions
(`?c rdfs:subClassOf [ owl:onProperty ?R ; owl:someValuesFrom ?filler ]`), **not** as
direct triples (0 direct R-triples in the store; associations are direct A-triples). The
restriction-traversal query (`ontolib` `terminologies/ncit/graph_store_role_queries.py`)
is the backbone that makes roles queryable, and the foundation the decomposition engine
builds on. Porting it faithfully is the keystone of M1/M2 ("roles must render").

## Decomposition model (additive; exact reversibility quarantined)

Legacy pre-coordinated concepts are **flagged, never deleted**
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
