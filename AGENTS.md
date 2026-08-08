# AGENTS.md

ONTOPRISM: an ontology exploration/decomposition platform over NCIt + caDSR
(FastAPI + Oxigraph/SPARQL + Postgres/pgvector backend, SvelteKit 5 frontend). See
`README.md` for product goals and `docs/ARCHITECTURE.md` for the full layout.

## Hard rules (never violate)

- **NEVER merge a PR unless CI is green on the target branch (`main`).** Before any
  `gh pr merge`, fetch `origin/main` and verify the newest push-event `CI` run on `main`
  completed successfully. Its head must equal `origin/main` or be its ancestor with only
  documented workflow-generated release/README commits in between. Confirm `origin/main`
  is an ancestor of the PR head; if not, update the branch and wait for its replacement
  checks. Run `gh pr view <number> --json title,headRefName,headRefOid,statusCheckRollup` and evaluate the newest run for each
  workflow/job name on the current head; superseded older runs may be ignored. Use `gh run list --workflow pr-title.yml --branch <headRefName> --event pull_request --json displayTitle,headSha,status,conclusion,createdAt` to confirm the newest run on that head is
  successful and its `displayTitle` exactly equals `Validate PR title: <title>`. The expected
  checks are all nine `CI` jobs, `conventional commit subject`, `dependency review`, all
  three configured `Analyze (...)` CodeQL jobs, and the aggregate `CodeQL` check. Verify
  `CI summary` is `"SUCCESS"`. Every other check must be `"SUCCESS"` or `"SKIPPED"` solely
  because of a documented path condition, including a dependent job skipped when its
  path-gated prerequisite did not run. If any expected check is absent, or any check failed,
  was cancelled, is pending, or was unexpectedly skipped, *stop* — ask the user before
  proceeding.
  **Documented exception (confirmed 2026-08-07):** on PRs that change only dependency
  manifests (`package.json`, `package-lock.json`) or only workflow files, CodeQL default setup
  does not run the three `Analyze (...)` jobs and posts the aggregate `CodeQL` check as
  `"NEUTRAL"`. That combination — Analyze jobs absent **and** aggregate `NEUTRAL` — is expected
  for those two PR shapes and is not a reason to stop. It is *not* expected on any PR touching
  source code, where all three Analyze jobs must be present and `"SUCCESS"`. Note also that
  `statusCheckRollup` sometimes omits `conventional commit subject` even when it has passed;
  confirm with the `gh run list --workflow pr-title.yml` command above rather than treating the
  omission as an absent check.
- **After merging any PR to `main`, watch CI and all triggered post-merge workflows to
  completion.** If any run fails, fix it before starting new work. Do not begin the next
  issue, create its branch, or open its PR while required post-merge runs are pending or
  failing.
- **`pdm run test-ci` must pass locally (or match CI outcome) before pushing CI changes.**
  If you can't reproduce a CI-only failure, isolate it from xdist rather than guessing.
- **`main` is protected by a ruleset: no force-pushes, no deletion.** Never attempt to
  rewrite or delete `main`. Land all work through PRs (see D30). Require-PR/required-CI
  enforcement is intentionally *not* enabled yet — it needs a release-bot credential as a
  ruleset bypass actor, else it would block the `GITHUB_TOKEN` release/README pushes.

## Verify your own handover specs before delegating (learned 2026-08-07, the hard way)

When you author a brief for another session to execute blindly, **apply the same evidence standard
to your own instructions that you apply to everyone else's output.** Three defects in one handover
cycle all came from skipping cheap local checks in a document written for blind execution — not
from hard judgement calls:

- A task named the wrong file, because the function's location was *inferred* from a finding's
  prose instead of grepped.
- A task said to regenerate an artifact that has no generator, because it was *assumed* to be
  derived from its filename and version number. One `rg` for its name returns nothing.
- A task specified a constraint ("a proposed member is not a missing member") instead of a target
  shape, so the executor reused an existing variant and weakened a type invariant to make it fit.

Before handing over a spec, check all four:

1. **Every file path and symbol in it exists.** Grep for the definition; do not infer location
   from surrounding prose.
2. **Every artifact you say to regenerate has a generator.** If nothing produces it, it is
   hand-authored and must not be rebuilt.
3. **Every type change states the target shape, not a constraint.** "Add variant `X` to union `Y`",
   never "`A` is not a `B`" — the latter leaves the executor to invent the positive form.
4. **Every acceptance check is sufficient, not merely necessary.** If the check can pass while the
   work is wrong, it is not a check. This is the same gate-liveness standard the project applies to
   its own tests, and it applies to handover briefs too.

Also state which tasks interact. Two tasks that touch the same type, listed independently and
several items apart, invite exactly the failure above.

## The one principle that keeps getting rediscovered (D60)

**Everything OntoPrism emits is NCIt.** Not NCIt blended with other ontologies — NCIt reorganised,
which is what makes it adoptable. A concept or role we introduce is NCIt content even when it
exactly matches, and was derived from, something in Uberon, Cell Ontology, SNOMED CT or ICD-O-3.

All of it is provisional until NCI adopts it: `proposed → locally-approved → submitted →
accepted-in-ncit`. `locally-approved` means *our* SME accepted it, not NCI.

Derivation is recorded as provenance and alignment, never as ownership — the pattern
`AxisContract.ro_parent` already uses, where `op:PrimarySite` is *our* relation and `RO:0004026` is
what it aligns to. Provenance exists so Metathesaurus integration and cross-terminology mapping
work, not as an audit ritual.

**Language rule:** never write "external content", "borrowed from" or "depends on" about anything
we emit. Write "derived from", "aligned to", "corroborated by", or "proposed, evidenced by".
Wording that implies another project owns part of our output has repeatedly misdirected design
decisions — see D60 for the full statement.

## Repo layout (keep-names, 3 project packages)

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
pdm run test-integration     # safe default: nonce-owned disposable PG/Oxigraph
pdm run test-integration-full-store  # explicit read-only contracts against configured corpora
pdm run test-ci              # strict gate: ontolib/src & backend/src each >90% line AND >90% branch (matches CI)
pdm run test-smoke           # frontend vitest via npm
```

- **Single test / focused run**: use the `pytest` console script via pdm —
  `pdm run pytest ontolib/tests/path/test_x.py::test_name -v`. Do **not** run
  `python -m pytest`: the module form prepends the repo root to `sys.path`, where the
  outer `ontolib/`/`backend/` dirs shadow the editable install (see `docs/DECISIONS.md`
  D6). `pdm run pytest` / the `pdm run test*` scripts use the correct console-script
  resolution; the root `conftest.py` also fixes `sys.path` for xdist workers.
  For a mutating/seeded integration node, retain the fail-closed lane:
  `pdm run python scripts/run_safe_integration.py <path>::<test> -v`.
  Run a read-only `full_store` node with `pdm run test-integration-full-store -k <name>`.
- Frontend single test: `cd frontend && npx vitest run <path>` (or `-t <name>`).
- Markers (registered in root `pyproject.toml`): `unit`, `api`, `security`,
  `integration` (real services), `mutating_integration` (nonce-owned disposable
  resources), `full_store` (read-only configured corpora), `full_build` (pinned
  12.8M-triple NCIt build / real embeddings — excluded from CI, run manually), `e2e`,
  `slow`.
- Never let a mutating integration test use `live_api_client`, `ncit_url`, or a
  configured persistent resource. Add it to `test_support/integration_mutators.toml`
  and request the exact isolated fixture. Required disposable-service failures fail;
  they never skip. Run applicable real-corpus contracts explicitly with
  `pdm run test-integration-full-store`; those contracts are read-only.
- **Strict TDD + coverage >90%** (line+branch) on `ontolib/src`, `backend/src`, and
  `frontend/src/lib` is a hard project rule, enforced by CI and a pre-commit
  test-quality hook that blocks mock-only / coverage-padding tests. Full test-quality
  rules are in `CLAUDE.local.md` — read it before writing tests.
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
- **Decomposition is additive/non-destructive, never mutating**: legacy pre-coordinated
  concepts are flagged (`representationStatus="legacy-precoordinated"`), never deleted;
  decomposed triples go in a separate `ncit_decomposed` named graph. Exact reversibility
  is quarantined until #153 provides a proof-bearing representation (D43). Extraction reads
  from the **stated** OWL (loaded via Oxigraph's offline bulk loader, not HTTP — the
  713MB stated build OOM-kills the container over HTTP GSP), not the inferred store.
- The frontend only ever talks to the FastAPI backend; the backend owns all
  Oxigraph/Postgres access — don't add direct DB/SPARQL access from `frontend/`.
- `pdm run data-build` (owl → cadsr → embeddings) rebuilds all data from public sources
  with no `fairdata` dependency; the embeddings step needs `pdm install -G data-build`
  (heavy ML extra, not installed by default).

## Conventions

- **Never commit directly to `main`.** All developer-authored code changes, issue
  implementations, and fixes must be on a dedicated branch
  (`feat/<slug>-<issue#>`, `fix/...`, `security/...`, `docs/...`) and land via PR. The only
  exceptions are the workflow-generated semantic-release and `Update README Code Stats`
  bot commits pushed by CI (with `GITHUB_TOKEN`).
- Branches: `feat/<slug>-<issue#>`, `fix/...`, `security/...`, `docs/...`; PRs merge into
  `main`.
- **PR bodies must only reference issues they fully resolve.** Use `Closes #X` /
  `Fixes #X` only when the PR completely resolves the issue (see D35). Issues labeled
  `epic` must never be referenced in a `Closes` keyword.
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
- **Pre-PR review fix cycle (mandatory, no exceptions): after implementation and local
  gates are complete, commit all intended changes on the feature branch. The worktree must
  be clean before review starts. Then, before PR creation, review the
  committed branch diff (`main...HEAD`) against current `main` with the FULL
  `pr-review-toolkit` agent set in the initial round — ALL FIVE, no cherry-picking. A
  review of staged or unstaged changes does not count toward convergence:**
  1. `pr-review-toolkit:code-reviewer` — correctness, guideline compliance
  2. `pr-review-toolkit:silent-failure-hunter` — swallowed errors, failures that look like
     clean results
  3. `pr-review-toolkit:pr-test-analyzer` — do the tests actually fail when the code is
     wrong, or do they agree with a fiction?
  4. `pr-review-toolkit:comment-analyzer` — do the docstrings claim guarantees the code
     does not provide?
  5. `pr-review-toolkit:type-design-analyzer` — are the invariants enforced by the types
     or only by the caller's good manners?

  **In every round `pr-test-analyzer` runs ALONE; the others run in parallel (see D49).**
  It mutates production code to prove a test fails when the code is wrong, so a
  concurrent reviewer can observe a dirty worktree and review code that is not the
  committed diff. That invalidates the round's clean-worktree precondition for every
  agent running beside it. Run the other non-converged agents in parallel, then
  `pr-test-analyzer` on its own against the same commit; it must restore every mutation.
  After it returns, verify `git status --porcelain` is empty **and** `git rev-parse HEAD`
  is unchanged before accepting its verdict or starting another round — a mutation that
  was committed or amended into `HEAD` leaves the tree clean while the reviewed diff has
  moved. If either check fails, restore the tree yourself and treat that run as
  inconclusive, hence non-converged. The full set otherwise matters because they find different classes of
  defect and do not substitute for one another. On #73 the five caught, respectively: a
  vacuous satisfiability gate, an environment failure laundered into a verdict, a test
  double that encoded a reasoner behaviour ELK does not have, docstrings asserting a D21
  guarantee the merge could not provide, and an invariant enforced only by convention.
  Running two of the five would have shipped the other three.

  Fix EVERY verifiable issue reported — critical, important, AND sensible suggestions
  (anything you can confirm and act on) — then re-run the applicable local gates and commit
  those fixes before re-running **only the non-converged agents** (if that reduced set
  includes `pr-test-analyzer`, it still runs by itself, after the others). Every review round must
  inspect a clean worktree and the committed `main...HEAD` diff. Do not create the
  PR until all five agents have converged and the final local gates pass. **Pushing the
  feature branch is a separate matter and is encouraged at any point** — `ci.yml` triggers only
  on `main` pushes and pull requests, so a branch push runs no workflows. It costs nothing, and
  it is the only backup for work that otherwise exists on one machine. The PR is what is delayed,
  for two reasons: a PR should present finished work rather than a moving target, and opening one
  early triggers the full check matrix repeatedly on every subsequent push. An agent converges
  only when a
  successfully completed full-diff review
  explicitly reports no unresolved actionable verified findings. An agent that reports
  any such finding remains non-converged whether the finding is new or repeated. Failed,
  timed-out, or inconclusive reviews also remain non-converged and must be retried. Once
  converged, an agent must not run again during that PR review cycle, even after another
  agent's fixes change the diff. Repeat the reduced, non-converged set until each
  remaining agent converges. Do not skip re-verification for an agent that found an
  issue, do not defer fixable issues, and do not merge with known-fixable findings
  outstanding. NO BUTS. The only findings you may leave are ones genuinely not
  verifiable/actionable in this repo — record each exception explicitly, with the
  reason.**
- **Ephemeral planning/handover docs live in `tmp/plans/` (gitignored), never tracked.**
  Plan-mode plan files and any implementation handover written for a follow-up session go
  under `./tmp/plans/`, not in `.opencode/plans/` or `docs/`. Durable knowledge belongs in
  the tracked docs (`docs/DECISIONS.md`, `docs/design/`) and the GitHub
  issues; never reference a `tmp/` path from a tracked file or a GitHub issue.
