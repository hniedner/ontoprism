# ONTOPRISM

**Pre-coordination Refactoring Into Semantic Modules.** ONTOPRISM is an ontology exploration
and refactoring platform for the cancer-research domain. It provides a rich interface over the
**NCIt** (NCI Thesaurus) and **caDSR** (Common Data Elements) repositories, and — its distinctive
purpose — refracts NCIt's **pre-coordinated** concepts into their **atomic (non-pre-coordinated)**
constituents so complex meaning can be *composed* rather than baked into the terminology.

## Goals

1. **A rich interface to explore NCIt and caDSR — including graph exploration.**
   Search and browse both repositories, inspect concept/CDE detail (roles, associations,
   permissible values, hierarchy), traverse the bidirectional NCIt↔caDSR cross-links, and
   explore the concept network in an interactive force-directed graph with community detection
   and centrality analysis.

2. **A decomposed ("atomic") NCIt.** Map every pre-coordinated NCIt concept to its constituent
   simple (atomic, non-pre-coordinated) concepts — roles-first from NCIt's restriction axioms,
   with a linguistic fallback. The transformation is **additive and reversible**: original
   concepts are retained and flagged `legacy-precoordinated`, the decomposition lives in a
   separate `ncit_decomposed` named graph, and existing caDSR CDE→concept mappings are preserved.
   → [#4](https://github.com/hniedner/ontoprism/issues/4)

3. **A balanced concept graph.** Rebalance the decomposed network so it *fully and evenly*
   represents the cancer-research domain — comparable concept density across all sub-domains,
   expressed purely in atomic concepts, without the density distortions that pre-coordination
   introduces. → [#5](https://github.com/hniedner/ontoprism/issues/5)

4. **A post-coordination expression syntax.** A grammar for composing atomic concepts + roles
   into complex (post-coordinated) concepts that can be **applied to observations and findings** —
   parseable to the atomic graph and round-trippable back to the equivalent pre-coordinated NCIt
   concept. → [#6](https://github.com/hniedner/ontoprism/issues/6)

The name is the method: a **prism** takes pre-coordinated "white light" concepts and refracts
them into their atomic spectrum, from which any complex concept can be recomposed on demand.

## Status

**Working today** (goal 1): a professional dark/light SvelteKit UI over both repositories —

- **NCIt**: search + no-search browse (natural order), sortable/paginated result tables, concept
  detail (definition, semantic types, synonyms, parents/children, roles, associations, incoming
  roles), mapped caDSR CDEs, and embedding-based similar concepts.
- **caDSR**: search + browse, CDE detail with NCIt concept cross-links (ISO-11179 roles),
  permissible values, and similar CDEs.
- **Interactive graph explorer**: Sigma/graphology force-directed network with Louvain community
  detection, degree centrality, click-to-select, expand-on-demand, node search/focus, zoom/fit,
  fullscreen, color modes, and a live network-stats panel — alongside a lightweight radial view.
- **Backend**: FastAPI over an Oxigraph SPARQL store (concept detail with role traversal, search,
  browse, neighborhood, guarded SPARQL) + a caDSR SQLite repository + 768-dim pgvector embeddings
  for semantic similarity, with a repository refresh/reload capability.

**Next** (goals 2–4): the decomposition engine, graph balancing, and expression syntax are the
science ahead — tracked in [#4](https://github.com/hniedner/ontoprism/issues/4), [#5](https://github.com/hniedner/ontoprism/issues/5), [#6](https://github.com/hniedner/ontoprism/issues/6).

## Roadmap & open work

| Area | Issue |
|---|---|
| NCIt decomposition (pre-coordinated → atomic) | [#4](https://github.com/hniedner/ontoprism/issues/4) |
| Concept-graph balancing across domains | [#5](https://github.com/hniedner/ontoprism/issues/5) |
| Post-coordination expression syntax | [#6](https://github.com/hniedner/ontoprism/issues/6) |
| Graph explorer feature parity (minimap, context menu, layouts, export…) | [#1](https://github.com/hniedner/ontoprism/issues/1) |
| Pre-commit hook parity | [#2](https://github.com/hniedner/ontoprism/issues/2) |
| Test runner with summary table + by-type organization | [#3](https://github.com/hniedner/ontoprism/issues/3) |

## Running the app (dev)

ONTOPRISM runs its own isolated data services (ports distinct from the sibling `fairdata` app —
see `docs/DATA_SETUP.md`), so both can run at once.

```bash
pdm install            # Python deps (Python 3.13)
pdm run up             # start data services: oxigraph :7888/:7889, postgres :5433
pdm run start-all      # start backend (:8011) + frontend (:5175) in the background
# → open http://localhost:5175
```

Process commands (fairdata-style):

| Command | Does |
|---|---|
| `pdm run start-all` / `stop-all` / `restart-all` | backend + frontend |
| `pdm run start-backend` / `stop-backend` / `restart-backend` | FastAPI on :8011 |
| `pdm run start-frontend` / `stop-frontend` / `restart-frontend` | SvelteKit on :5175 |
| `pdm run up` / `down` | data-service containers (docker compose) |
| `pdm run api-dev` / `web-dev` | run one service in the foreground |

Background logs are written to `.dev-logs/`. First run provisions data per `docs/DATA_SETUP.md`.

## Testing

```bash
pdm run test              # backend + ontolib, non-integration (sharded)
pdm run test-unit         # unit-marked only
pdm run test-integration  # against the live Oxigraph/pgvector stores
pdm run test-ci           # CI config with coverage gate (≥80%)
pdm run test-smoke        # frontend vitest
```

Local gate: `pdm run pre-commit run --all-files` (CI mirrors it). Richer per-type test reporting
is tracked in [#3](https://github.com/hniedner/ontoprism/issues/3).

## Stack

Python 3.13 · PDM · FastAPI · Oxigraph (SPARQL) · PostgreSQL + pgvector · SvelteKit 5 ·
Tailwind 4 · Sigma + graphology · ruff · basedpyright · pytest · vitest
<!-- CODEBASE_LINE_COUNT_TABLE:START -->
## Codebase Line Count

_This table is auto-updated by CI after successful builds on `main`._

| Language | Files | Lines |
| --- | ---: | ---: |
| Python | 156 | 19,063 |
| JSON | 4 | 5,770 |
| TypeScript | 61 | 4,169 |
| Svelte | 38 | 3,192 |
| Markdown | 17 | 2,543 |
| CSS | 3 | 1,993 |
| YAML | 6 | 624 |
| TOML | 3 | 323 |
| Shell | 1 | 95 |
| JavaScript | 1 | 38 |
| HTML | 1 | 21 |
| **Total** | **291** | **37,831** |
<!-- CODEBASE_LINE_COUNT_TABLE:END -->

## Provenance

ONTOPRISM lifts the ontology vertical slice from the sibling `fairdata` codebase (whole-package
port of `ontolib`, `backend`, and `frontend`), deliberately leaving behind fairdata's
pipeline/HRM/learning/audit subsystems. See `docs/DECISIONS.md`.
