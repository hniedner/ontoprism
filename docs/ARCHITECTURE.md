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
│   │   ├── storage/          #   Oxigraph HTTP store base, pyoxigraph compat
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

- **Oxigraph (SPARQL)** — the ontology graph. Source NCIt graph is read-only; the
  decomposition engine writes a separate `ncit_decomposed` named graph (additive, never
  mutating the source). NCIt on :7888, Uberon on :7889 (Postgres :5433) — ports are
  offset from the sibling `fairdata` app so both can run at once.
- **PostgreSQL** — concept metadata/FTS cache, decomposition run state, provenance
  (`decomp_run`, `decomp_constituent`, `minted_concept`), and the caDSR read tables.

The frontend talks only to the FastAPI backend; the backend owns all Oxigraph/Postgres
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
genus/restriction group, and stable trace links from projected constituents (D50).
PostgreSQL and the additive RDF artifact both round-trip groups, review flags, and those
facts. The graph still never emits `owl:equivalentClass`; requests for equivalence fail
closed and new runs record no round-trip-fidelity value until a separate proof/validation
step establishes exact semantics (D43/D50). Most-specific collapse applies only to the
projection and never changes the complete record.

`op:PrimarySite` is anatomy-valued and cardinality `0..1` on both a disease-class
projection and a future cancer-disease occurrence. Absence in the class projection is not
CUP: site-agnostic morphology classes simply do not pose the question. The future
occurrence model must instead carry exactly one `primarySiteStatus` (`known`,
`unknown-cup`, `undetermined`, or `not-applicable`), with `known` iff a primary-site filler
is present. Patients may have any number of disease occurrences; occurrence individuation
(second primary versus metastasis) is upstream of this constraint and must never be
inferred from the site cardinality. The current class projection derives status from its
site plus explicit complete-record unknown-primary evidence but does not persist it (D58).
The future occurrence schema and API are tracked in #263.

See the [design docs](design/) — the [decomposition assessment](design/ncit-decomposition-assessment.md)
(the *why* + verified prevalence numbers) and the [engine design](design/ncit-decomposition-engine.md)
(the *how*) — for the full rationale.
