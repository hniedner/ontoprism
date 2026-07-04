# ONTOPRISM

**Pre-coordination Refactoring Into Semantic Modules** — an ontology storage, query, and
graph-visualization platform whose distinctive purpose is to produce a **decomposed
(non-pre-coordinated) NCIt**.

## What it does

1. **Storage + visualization platform** — ported from the sibling `fairdata` codebase: an Oxigraph
   graph store, a FastAPI backend (concept detail with roles/associations, search, graph
   neighborhood, guarded SPARQL), and a SvelteKit 5 frontend (query interface with result table +
   graph explorer). Houses the **NCIt** and **caDSR** repositories.
2. **Decomposed NCIt** — an engine that replaces pre-coordinated concepts with their
   post-coordinated constituents (roles-first, NLP fallback), **additively and reversibly**: every
   original concept is retained and flagged `legacy-precoordinated`, written to a separate
   `ncit_decomposed` named graph. Existing caDSR CDE→concept mappings are preserved.

## Running the app (dev)

ontoprism runs its own isolated data services (ports distinct from the sibling fairdata app —
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

## Status

NCIt + caDSR repository webapp is functional (search, concept/CDE detail, roles, graph explorer,
bidirectional cross-links, 768-dim embeddings for semantic similarity, and refresh). The
decomposition engine (the "prism" science) is the next milestone. Plan/assessment are kept under
`tmp/` (untracked); decisions in `docs/DECISIONS.md`.

## Stack

Python 3.13 · PDM · FastAPI · SvelteKit 5 · Oxigraph · PostgreSQL · ruff · basedpyright · pytest · vitest
