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
├── docker-compose.yml        # postgres + oxigraph-ncit(:7878) + oxigraph-uberon(:7879)
├── Makefile  .env.example
├── .github/workflows/ci.yml  # backend (ruff+basedpyright+cov) + web (eslint+check+vitest)
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
│   │   └── api/…/routers/    #   repo/graph/search/sparql/refresh + decomp (M6)
│   └── tests/
├── frontend/                 # LIFTED SvelteKit 5 app (M4)
└── docs/  ARCHITECTURE.md  DECISIONS.md  DATA_SETUP.md  design/
```

## Data planes

- **Oxigraph (SPARQL)** — the ontology graph. Source NCIt graph is read-only; the
  decomposition engine writes a separate `ncit_decomposed` named graph (additive, never
  mutating the source). NCIt on :7878, Uberon on :7879.
- **PostgreSQL** — concept metadata/FTS cache, decomposition run state, provenance
  (`decomp_run`, `decomp_constituent`, `minted_concept`), and the caDSR read tables.

The frontend talks only to the FastAPI backend; the backend owns all Oxigraph/Postgres
access.

## Key inherited mechanism: NCIt roles are OWL restrictions

NCIt encodes pre-coordination as OWL existential restrictions
(`?c rdfs:subClassOf [ owl:onProperty ?R ; owl:someValuesFrom ?filler ]`), **not** as
direct triples (0 direct R-triples in the store; associations are direct A-triples). The
restriction-traversal query (`ontolib` `terminologies/ncit/graph_store_role_queries.py`)
is the backbone that makes roles queryable, and the foundation the decomposition engine
builds on. Porting it faithfully is the keystone of M1/M2 ("roles must render").

## Decomposition model (canonical, additive, reversible)

Legacy pre-coordinated concepts are **flagged, never deleted**
(`representationStatus="legacy-precoordinated"`) and linked to constituents via
`hasConstituent[axis, filler]`. Constituents come roles-first (100% already exist as
active concepts) with NLP/label parsing as fallback for label-only axes (laterality,
with/without <finding>, staging-manual version). Scope: disease/neoplasm(/regimen);
gene/protein role families are excluded by a semantic-type gate. Extraction runs off the
**stated** OWL (DECISIONS D4); the inferred store is used only for validation/closure.

Reversibility (README goal 4) rests on a **complete, lossless representation of record** —
the full multi-parent-DAG unfolding of a concept's `owl:equivalentClass` definition, with
genuinely co-equal axes kept multi-valued as SNOMED-style relationship groups (DECISIONS
D19). The single most-specific filler per axis is a deliberately **lossy curated projection**
on top of that record, for human legibility, not the round-trippable artifact; most-specific
collapse applies only to *nested* (is-a/part-of) candidates, never to non-nested co-equal
values such as site-vs-lineage (DECISIONS D19/D20).

See the [design docs](design/) — the [decomposition assessment](design/ncit-decomposition-assessment.md)
(the *why* + verified prevalence numbers) and the [engine design](design/ncit-decomposition-engine.md)
(the *how*) — for the full rationale.
