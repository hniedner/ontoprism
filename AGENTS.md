# AGENTS.md

ONTOPRISM: an ontology exploration/decomposition platform over NCIt + caDSR
(FastAPI + Oxigraph/SPARQL + Postgres/pgvector backend, SvelteKit 5 frontend). See
`README.md` for product goals and `docs/ARCHITECTURE.md` for the full layout.

## Repo layout (keep-names, 3 installable packages)

- `ontolib/` — shared library, import name `ontolib` (storage, NCIt/Uberon
  terminologies, caDSR repository, decomposition engine). Editable install.
- `backend/` — FastAPI app, import name `backend` (`backend/src/backend/main.py` +
  `api/.../routers`). Editable install.
- `frontend/` — SvelteKit 5 app, separate npm project in `frontend/`.
- Root `pyproject.toml` holds all `pdm run` scripts, ruff/basedpyright/coverage
  config, and pytest markers — the sub-packages have their own minimal `pyproject.toml`
  but you run everything from the repo root.
- `docs/DECISIONS.md` is a running decision log (numbered D1, D2, …) — check it before
  changing import/test setup, versions pins, or the decomposition model; it explains
  *why*, not just *what*.

## Setup & dev servers

```bash
pdm install --dev       # Python 3.13, installs ontolib + backend editable
npm ci --prefix frontend
pdm run up               # docker compose: oxigraph-ncit :7888, oxigraph-uberon :7889, postgres :5433
pdm run migrate          # Alembic — fresh DB only; use `migrate-stamp` on a pre-existing cloned DB
pdm run start-all        # backend :8011 + frontend :5175 in background, logs in .dev-logs/
```

Ports are deliberately offset from the sibling `fairdata` app (8001/5173/7878/7879/5432)
so both can run at once — see `docs/DATA_SETUP.md`. Copy `.env.example` → `.env` first;
defaults point at the services above.

## Testing

```bash
pdm run test                # grouped hermetic suites (backend unit/api/security + frontend vitest)
pdm run test-unit            # unit-marked only, backend+ontolib
pdm run test-integration     # needs live Oxigraph :7888/Postgres :5433 (pdm run up)
pdm run test-ci              # coverage gate, --cov-fail-under=90 (matches CI)
pdm run test-smoke           # frontend vitest via npm
```

- **Single test / focused run**: use the `pytest` console script via pdm —
  `pdm run pytest ontolib/tests/path/test_x.py::test_name -v`. Do **not** run
  `python -m pytest`: the module form prepends the repo root to `sys.path`, where the
  outer `ontolib/`/`backend/` dirs shadow the editable install (see `docs/DECISIONS.md`
  D6). `pdm run pytest` / the `pdm run test*` scripts use the correct console-script
  resolution; the root `conftest.py` also fixes `sys.path` for xdist workers.
- Frontend single test: `cd frontend && npx vitest run <path>` (or `-t <name>`).
- Markers (registered in root `pyproject.toml`): `unit`, `api`, `security`,
  `integration` (real services), `full_build` (pinned 12.8M-triple NCIt build / real
  embeddings — excluded from CI, run manually), `e2e`, `slow`.
- **Strict TDD + coverage >90%** (line+branch) on `ontolib/src`, `backend/src`, and
  `frontend/src/lib` is a hard project rule, enforced by CI and a pre-commit
  test-quality hook that blocks mock-only / coverage-padding tests. Full rules and the
  two documented coverage exceptions (NCIt SPARQL parsing layer; sigma/canvas
  components not mountable in jsdom) are in `CLAUDE.local.md` — read it before writing
  tests.
- Frontend gotcha: fire-and-forget rejections inside a Svelte `$effect` trip vitest's
  unhandled-rejection guard on mock reset between tests — use `mockClear`, not
  `mockReset` (see `CLAUDE.local.md`).

## Quality gates

Pre-commit is the primary gate; CI just replays it (`pdm run pre-commit run
--all-files`) plus the test/coverage jobs. Order matters only in that pre-commit runs
fixers before checks — locally just run:

```bash
pdm run lint    # ruff check + basedpyright (full project)
pdm run fmt     # ruff format
```

Frontend hooks (`cd frontend`): `npx eslint src/ --max-warnings=0`, `npm run check`
(svelte-check), `npm run fallow` (cross-file dead-code/cycle/duplication gate — only
fails on findings introduced vs `origin/main`, needs full git history).

## Architecture notes not obvious from the code

- **NCIt roles are OWL existential restrictions**, not direct triples
  (`?c rdfs:subClassOf [owl:onProperty ?R; owl:someValuesFrom ?filler]`). The
  restriction-traversal query in `ontolib/src/ontolib/terminologies/ncit/` is what
  makes roles queryable at all — associations, by contrast, *are* direct triples.
- **Decomposition is additive/reversible, never mutating**: legacy pre-coordinated
  concepts are flagged (`representationStatus="legacy-precoordinated"`), never deleted;
  decomposed triples go in a separate `ncit_decomposed` named graph. Extraction reads
  from the **stated** OWL (loaded via Oxigraph's offline bulk loader, not HTTP — the
  713MB stated build OOM-kills the container over HTTP GSP), not the inferred store.
- The frontend only ever talks to the FastAPI backend; the backend owns all
  Oxigraph/Postgres access — don't add direct DB/SPARQL access from `frontend/`.
- `pdm run data-build` (owl → cadsr → embeddings) rebuilds all data from public sources
  with no `fairdata` dependency; the embeddings step needs `pdm install -G data-build`
  (heavy ML extra, not installed by default).

## Conventions

- Branches: `feat/<slug>-<issue#>`, `fix/...`, `docs/...`; PRs merge into `main`.
- **PR titles must be Conventional Commits** (`type(scope)?!?: subject`) — CI enforces
  this (`.github/workflows/pr-title.yml`), because the release workflow derives the
  version bump from them. `feat` → minor, `fix`/`perf` → patch, `!` or a
  `BREAKING CHANGE:` footer → minor (this project is pre-1.0; see D18). Every other
  type (`docs`, `chore`, `test`, `ci`, `refactor`, `style`, `build`, `security`) lands
  in the changelog without bumping the version.
- **Do not hand-edit `CHANGELOG.md`.** From `v0.7.0` on it is generated by
  python-semantic-release on merge to `main`; sections below the `<!-- version list -->`
  flag are reconstructed history. Write the changelog by writing good commit subjects.
- Versions live in five manifests and are stamped automatically on release — never bump
  them by hand.
- **Ephemeral planning/handover docs live in `tmp/plans/` (gitignored), never tracked.**
  Plan-mode plan files and any implementation handover written for a follow-up session go
  under `./tmp/plans/`, not in `.opencode/plans/` or `docs/`. Durable knowledge belongs in
  the tracked docs (`docs/ROADMAP.md`, `docs/DECISIONS.md`, `docs/design/`) and the GitHub
  issues; never reference a `tmp/` path from a tracked file or a GitHub issue.
