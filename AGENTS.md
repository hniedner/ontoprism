# AGENTS.md

ONTOPRISM: an ontology exploration/decomposition platform over NCIt + caDSR
(FastAPI + Oxigraph/SPARQL + Postgres/pgvector backend, SvelteKit 5 frontend). See
`README.md` for product goals and `docs/ARCHITECTURE.md` for the full layout.

## Hard rules (never violate)

- **NEVER merge a PR unless CI is green on the target branch (`main`).** Before any
  `gh pr merge`, run `gh pr view <number> --json statusCheckRollup` and verify every
  conclusion is `"SUCCESS"`. If any check failed or is pending, *stop* — ask the user
  before proceeding.
- **After merging any PR to `main`, watch the CI run to completion.** If it fails, fix
  it before starting any new work. Do not begin Phase B tasks, create branches, or open
  PRs while `main` CI is red.
- **`pdm run test-ci` must pass locally (or match CI outcome) before pushing CI changes.**
  If you can't reproduce a CI-only failure, isolate it from xdist rather than guessing.
- **`main` is protected by a ruleset: no force-pushes, no deletion.** Never attempt to
  rewrite or delete `main`. Land all work through PRs (see D30). Require-PR/required-CI
  enforcement is intentionally *not* enabled yet — it needs a release-bot credential as a
  ruleset bypass actor, else it would block the `GITHUB_TOKEN` release/README pushes.

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
pdm run test-ci              # per-package coverage gate: ontolib/src & backend/src each >=90% (matches CI)
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
- **TDD does NOT catch false assumptions about external systems. Three extra test types
  are mandatory whenever code depends on an external tool, library, or real data.**
  Learned the hard way on #73 (PR #117): ~12 bugs shipped past a green, strictly-TDD'd
  suite, and **not one was a logic error in our code** — every one was a false belief
  about ROBOT's CLI, ELK's output shape, asyncpg, OWL/RDF serialization, or the real
  Uberon data. The mechanism: *the test and the code are written from the same mental
  model, so the hand-made double encodes the same false belief as the implementation.
  They agree with each other, both are wrong, and the suite is green.* Three of the worst
  bugs were actively **certified** by a test double implementing a rule the real tool does
  not. So:
  1. **Contract tests** — assert what the *external tool itself* does, not what our
     wrapper does (`test_reasoner_contract.py`). A tool upgrade then fails loudly and
     names the broken assumption, instead of surfacing months later as "no candidate
     qualified".
  2. **Double-fidelity tests** — run the *same* input through the double and the real
     thing; assert they reach the same verdict. A double *stronger* than reality certifies
     guards that do not exist; a double *weaker* than reality hides gates that cannot fire.
  3. **Data-shape contract tests** — pin what the *real* store actually looks like
     (`test_upstream_data_contract.py`). Fixtures encode only what their author believed:
     Uberon relates organ→system by `part_of`, not `subClassOf`, and assuming otherwise
     made a veto fire on the canonical *correct* mapping.

  Plus **gate liveness**: for every gate, prove its *reject* branch is reachable on
  production-shaped input. #73's satisfiability gate was vacuous for a whole round — it
  could never fire — and every happy-path test still passed.
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

Security gates (public repo, see D30/D31): `zizmor` pre-commit hook lints workflow security
(unpinned actions, excessive `GITHUB_TOKEN` perms, credential persistence) — keep actions
SHA-pinned and Docker base images digest-pinned. CI also runs CodeQL (default setup),
dependency-review, and OpenSSF Scorecard; Dependabot (github-actions/npm/docker, 7-day
cooldown) + secret scanning + push protection are enabled repo-side.

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

- **Never commit directly to `main`.** All code changes, issue implementations, and fixes
  must be on a dedicated branch (`feat/<slug>-<issue#>`, `fix/...`, `docs/...`) and land
  via PR. The only exception is the auto-generated `Update README Code Stats` bot commit
  pushed by CI (with `GITHUB_TOKEN`).
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
- **PR review fix cycle (mandatory, no exceptions): after creating a PR, review the diff
  with the FULL `pr-review-toolkit` agent set — ALL FIVE, every round, no cherry-picking:**
  1. `pr-review-toolkit:code-reviewer` — correctness, guideline compliance
  2. `pr-review-toolkit:silent-failure-hunter` — swallowed errors, failures that look like
     clean results
  3. `pr-review-toolkit:pr-test-analyzer` — do the tests actually fail when the code is
     wrong, or do they agree with a fiction?
  4. `pr-review-toolkit:comment-analyzer` — do the docstrings claim guarantees the code
     does not provide?
  5. `pr-review-toolkit:type-design-analyzer` — are the invariants enforced by the types
     or only by the caller's good manners?

  **Run them in parallel; they find different classes of defect and they do not
  substitute for one another.** On #73 the five caught, respectively: a vacuous
  satisfiability gate, an environment failure laundered into a verdict, a test double
  that encoded a reasoner behaviour ELK does not have, docstrings asserting a D21
  guarantee the merge could not provide, and an invariant enforced only by convention.
  Running two of the five would have shipped the other three.

  Fix EVERY verifiable issue reported — critical, important, AND sensible suggestions
  (anything you can confirm and act on) — then push and **re-run all five**. Repeat until
  a round detects NO verifiable issues. Only then is the PR ready. Do not skip the
  re-verification step, do not defer fixable issues, do not merge with known-fixable
  findings outstanding. NO BUTS. The only findings you may leave are ones genuinely not
  verifiable/actionable in this repo — call those out explicitly, with the reason.**
- **Ephemeral planning/handover docs live in `tmp/plans/` (gitignored), never tracked.**
  Plan-mode plan files and any implementation handover written for a follow-up session go
  under `./tmp/plans/`, not in `.opencode/plans/` or `docs/`. Durable knowledge belongs in
  the tracked docs (`docs/ROADMAP.md`, `docs/DECISIONS.md`, `docs/design/`) and the GitHub
  issues; never reference a `tmp/` path from a tracked file or a GitHub issue.
