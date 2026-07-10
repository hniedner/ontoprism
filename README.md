# ONTOPRISM

**Pre-coordination Refactoring Into Semantic Modules.** An ontology exploration and
decomposition platform for the cancer-research domain over NCIt and caDSR.

ONTOPRISM refracts NCIt's **pre-coordinated** concepts into their **atomic** constituents so
complex meaning can be *composed* — expressed purely as combinations of simple concepts —
rather than baked into the terminology as thousands of named combinations.

## Quickstart

```bash
pdm install                      # Python 3.13 deps
npm ci --prefix frontend         # SvelteKit deps
pdm run up                       # data services (Oxigraph + Postgres)
pdm run start-all                # backend :8011 + frontend :5175
```

Open [localhost:5175](http://localhost:5175). See [docs/DATA_SETUP.md](docs/DATA_SETUP.md)
for first-run provisioning.

## Project structure

```
ontolib/      Shared library — storage, NCIt/Uberon terminologies,
│             caDSR repository, decomposition engine
backend/      FastAPI app — repo/graph/search/sparql/refresh + decomposition API
frontend/     SvelteKit 5 app — Sigma + graphology graph explorer, dark/light UI
docs/         Architecture, design docs, decisions, data setup
scripts/      Dev tooling, data build, decomposition CLI, research helpers
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full layout and data-flow diagram.

## Status

| Layer | Status | Docs |
|---|---|---|
| NCIt + caDSR explorer | **Working** — search, browse, concept detail, graph explorer, CDE cross-links | |
| Decomposition engine | **Working** — detector, extractor, writer, CLI (`pdm run decompose`) | [design docs](docs/design/) |
| Extractor curation | **In progress** — improving coverage beyond 3.24% | [#44](https://github.com/hniedner/ontoprism/issues/44) |
| Graph balancing | **Not started** — depends on trustworthy decomposition output | [#5](https://github.com/hniedner/ontoprism/issues/5) |
| Post-coordination grammar | **Not started** — depends on graph balancing | [#6](https://github.com/hniedner/ontoprism/issues/6) |

## Stack

| Category | Technologies |
|---|---|
| Backend | Python 3.13 · PDM · FastAPI · Oxigraph (SPARQL) · PostgreSQL + pgvector |
| Frontend | SvelteKit 5 · Tailwind 4 · Sigma + graphology · TypeScript |
| Quality | ruff · basedpyright · pytest · vitest · pre-commit · >90% coverage |

## Development

### Services

| Command | Action |
|---|---|
| `pdm run up` / `down` | Start/stop data containers (Oxigraph, Postgres) |
| `pdm run start-all` / `stop-all` / `restart-all` | Backend + frontend process supervision |
| `pdm run start-backend` / `stop-backend` / `restart-backend` | FastAPI on :8011 |
| `pdm run start-frontend` / `stop-frontend` / `restart-frontend` | SvelteKit on :5175 |
| `pdm run migrate` | Alembic schema migration |

Background logs go to `.dev-logs/`. Ports are offset from the sibling `fairdata` app — see
[docs/DATA_SETUP.md](docs/DATA_SETUP.md).

### Testing

```bash
pdm run test               # Hermetic: ontolib unit + backend unit/api/security + frontend vitest
pdm run test-unit          # Unit-marked only
pdm run test-integration   # Against live Oxigraph and Postgres
pdm run test-ci            # CI gate with ≥90% coverage
pdm run pre-commit run --all-files  # Local quality gate
```

### Architecture decisions

Key architectural decisions are documented in [docs/DECISIONS.md](docs/DECISIONS.md) (D1–D21)
and the [decomposition design series](docs/design/).

<!-- CODEBASE_LINE_COUNT_TABLE:START -->
## Codebase Line Count

_This table is auto-updated by CI after successful builds on `main`._

| Language | Files | Lines |
| --- | ---: | ---: |
| Python | 158 | 19,544 |
| JSON | 4 | 5,770 |
| TypeScript | 61 | 4,169 |
| Svelte | 38 | 3,192 |
| Markdown | 17 | 2,525 |
| CSS | 3 | 1,993 |
| YAML | 7 | 665 |
| TOML | 3 | 323 |
| Shell | 1 | 95 |
| JavaScript | 1 | 38 |
| HTML | 1 | 21 |
| **Total** | **294** | **38,335** |
<!-- CODEBASE_LINE_COUNT_TABLE:END -->

## Provenance

ONTOPRISM lifts the ontology vertical slice from the sibling `fairdata` codebase
(whole-package port of `ontolib`, `backend`, and `frontend`), deliberately leaving behind
fairdata's pipeline/HRM/learning/audit subsystems. See [docs/DECISIONS.md](docs/DECISIONS.md).
