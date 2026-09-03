# AGENTS.md

ONTOPRISM: an ontology exploration/decomposition platform over NCIt + caDSR
(FastAPI + QLever/SPARQL + Postgres/pgvector backend, SvelteKit 5 frontend). See
`README.md` for product goals and `docs/ARCHITECTURE.md` for the full layout.

## Hard rules (never violate)

- **Pre-production carries no dead code or legacy compatibility code.** Transitional
  compatibility code is permitted only while an active refactor needs it. Remove it
  before the refactor is considered complete, before local gates are accepted, and
  before merge. Rebuild internal data/artifacts instead of retaining old-schema readers,
  adapters, fallbacks, migration shims, or deprecated branches. Do not preserve code for
  hypothetical rollback or future consumers while the product is pre-production.

- **Implement acceptance semantics end to end on the first pass.** Before production
  code, enumerate every required output field, semantic variant, refusal, and scale
  boundary from the issue. Tests must drive each item through storage/query → backend
  DTO → frontend rendering. Do not erase distinctions to reuse a component (for example,
  mapping typed edge kinds to a generic kind). For repository work, the certified
  identity must cover the exact values served, not only release labels, counts, or
  sentinels. Validate user input separately from source data so only dedicated input
  errors become 4xx responses; malformed source rows fail closed. Exercise the real
  highest-fanout record and assert bounded query count before accepting the design.

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
  An agent may run `gh pr merge` only after the user explicitly authorizes that exact PR
  number in the current conversation. Re-read the PR immediately before merging; any head,
  title, base, or merge-state change invalidates the authorization. Use squash merge with the
  exact conventional PR title and delete the merged branch. Never use `--admin`, auto-merge,
  a queue, or any bypass. Without that exact current-conversation authorization, stop before
  the merge command.
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

## Inference must never be written in the form of observation (2026-08-07, re-learned worse 2026-08-08)

The checklist at the end of this section was added 2026-08-07 and broken the next day, five times,
by the same session that wrote it. **That is the important datum: prose rules addressed to a future
reader do not bind the writer at the moment of writing.** Only a format that cannot be filled in
without running something binds. If you are about to add a rule here because a rule here was
broken, stop — you are adding ceremony, and ceremony is the disease.

**Root cause of every defect in that cycle: inference was written in the same form as observation.**
A digest that was computed and a digest that was remembered look identical on the page. A step that
was executed end-to-end and a step that was merely assumed to work look identical. Neither writer
nor reader can then tell which claims are load-bearing guesses. Actual instances: a workbook digest
restated from memory that did not match the file; an acceptance step ("run the report → satisfies
AC7") whose third argument did not exist and has no generator; a required input (the proposal
registry) omitted from that same step; a "NOT FOUND" produced by `rg` silently skipping gitignored
paths; and worst, a human SME asked to sign an attestation on the claim that closure was one step
away, when it was structurally blocked.

Mechanical rules. Each is checkable by the reader, which is what forces the check when writing:

1. **Every factual claim carries the command that produced it, inline.** Not "digest is `abc…`" but
   "digest is `abc…` (`shasum -a 256 <path>`, 2026-08-08)". No command → delete the claim. Never
   restate a hash, count, or status from memory or from an earlier document.
2. **An execution step names every argument and proves each exists.** "Run `golden_review.py`" is
   not a step. `f(a, b, c)`, with each of `a`, `b`, `c` shown present by an `ls`/`rg`, is a step.
   If any input is missing the step is `BLOCKED`, not pending.
3. **Never pre-declare the hash of an artifact that does not yet exist.** Bind identities *after*
   generation. A hash over a payload containing `uuid4()` can never be matched by a later run —
   `corpus_evidence_identity` cost a full cycle proving exactly that.
4. **Search artifacts with `rg --no-ignore`.** `tmp/` is gitignored (`.gitignore:2`), so a default
   `rg` reports absent files that are present and gives no signal that it skipped anything.
5. **Never request an irreversible human sign-off before executing the step that follows it.**
   Dry-run the downstream path first. An attestation spent on an unverified critical path is the
   one thing you cannot refund.

**When a rule here has failed twice, delete the complexity that made it necessary instead of
rewriting the rule.** Every defect above occurred inside an apparatus — chained identities, pinned
digests, two attestations, nineteen untracked artifacts — larger than the 20-concept measurement it
served. Complexity forces inference; inference produced the errors.

Handover briefs additionally: every path and symbol exists (grep it, never infer from prose); every
artifact you say to regenerate has a generator; every type change states the target shape ("add
variant `X` to union `Y`"), never a constraint; every acceptance check is sufficient, not merely
necessary. State which tasks interact.

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
pdm install --dev       # Python 3.13–3.14 (3.13 production/default), editable packages
npm ci --prefix frontend
cp .env.example .env
pdm run python scripts/install_jena.py --install-dir "$PWD/.tools/jena-6.1.0"
export ONTOPRISM_JENA_DIR="$PWD/.tools/jena-6.1.0"
pdm run data-build owl
pdm run data-build ncit-bootstrap
pdm run data-build uberon-store
pdm run up               # Compose via current selected Docker context; run `pdm run agent-replay activate-podman-docker-context` for supported Podman
pdm run migrate          # Alembic — fresh DB only; use `migrate-stamp` on a pre-existing cloned DB
pdm run start-all        # backend :8011 + frontend :5175 in background, logs in .dev-logs/
```

Ports are deliberately offset from the sibling `fairdata` app (8001/5173/7878/7879/5432)
so both can run at once — see `docs/DATA_SETUP.md`. Copy `.env.example` → `.env` first;
defaults point at the services above.

## Testing

```bash
pdm run verify              # THE pre-PR gate: everything CI enforces, in CI's own commands
pdm run test                # grouped hermetic suites (backend unit/api/security + frontend vitest)
pdm run test-unit            # unit-marked only, backend+ontolib
pdm run test-integration     # safe default: nonce-owned disposable PG/QLever
pdm run test-integration-full-store  # explicit read-only contracts against configured corpora
pdm run test-ci              # strict gate: ontolib/src & backend/src each >90% line AND >90% branch (matches CI)
pdm run test-smoke           # frontend vitest via npm
```

- **CI is the last bar, never the discovery mechanism. "Gates green" means `pdm run verify`
  exited 0** — not a subset of it. On PR #290 three defects reached CI because targeted
  substitutes were run instead of the real gate commands: a test asserting raw Rich `--help`
  output (CI enables colour, which injects ANSI escapes inside an option name), a
  `package.json` script that grew a `pdm` dependency the CI job never installed (invisible
  locally, where pdm is on PATH), and a ReDoS regex only CodeQL evaluates. A targeted
  `npx vitest run <file>` or a narrowed pytest selection is a debugging tool, not a gate.
- CodeQL is the one gate `verify` cannot reproduce (GitHub default setup, pull requests and
  `main` pushes only). Everything else is covered locally.

- **Single test / focused run**: use the repository's safe wrapper —
  `pdm run agent-test ontolib/tests/path/test_x.py::test_name -v`. Agents must never invoke raw
  `pdm run pytest` or `python -m pytest`: the wrapper constrains paths, flags, markers,
  environment, and subprocess execution, while the module form also prepends the repo
  root to `sys.path`, where the outer `ontolib/`/`backend/` dirs shadow the editable
  install (see `docs/DECISIONS.md` D6).
  For a mutating/seeded integration node, retain the fail-closed lane:
  `pdm run agent-test --safe-integration <path>::<test> -v`.
  Run a focused read-only `full_store` contract with
  `pdm run agent-test --full-store <node> -v`; the full aggregate remains `pdm run test-integration-full-store`.
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
- **Strict TDD means an observed RED run.** Before production edits, execute the exact
  behavioral test and confirm it fails for the intended missing/wrong contract. Never
  backfill tests after implementation and call that TDD. A declared suite collecting zero
  tests is a failed gate.
- **Every test must be a reliable regression indicator.** If a relevant production
  mutation does not make it fail for the right reason, delete or replace it. No execution-
  only tests, mock choreography, implementation-clone fakes, fixture self-consistency, or
  assertions added solely to satisfy coverage/test-quality tooling.
- **Double-only acceptance is forbidden.** PostgreSQL/schema, QLever, persisted JSON,
  external HTTP/tooling, adapter-node/browser, Docker, and filesystem changes require a
  real disposable/configured-boundary contract in addition to unit doubles. Run the
  applicable configured full-store and built-browser contracts before claiming the work
  complete; a green hand-authored double does not certify production behavior.
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
  from the **stated** OWL (stream-converted by pinned Jena RIOT and indexed by QLever's
  offline builder, never uploaded as the 713MB RDF/XML file over HTTP GSP), not the
  inferred store.
- The frontend only ever talks to the FastAPI backend; the backend owns all
  QLever/Postgres access — don't add direct DB/SPARQL access from `frontend/`.
- `pdm run data-build` (owl → cadsr → embeddings) rebuilds all data from public sources
  with no `fairdata` dependency; the embeddings step needs `pdm install -G data-build`
  (heavy ML extra, not installed by default).

## Conventions

- **Never commit directly to `main`.** All developer-authored code changes, issue
  implementations, and fixes must be on a dedicated branch
  (`feat/<slug>-<issue#>`, `fix/...`, `security/...`, `docs/...`) and land via PR. The only
  exceptions are the workflow-generated semantic-release and `Update README Code Stats`
  bot commits pushed by CI (with `GITHUB_TOKEN`).
- **Milestones use one integration branch and one final PR.** When work is scoped as a
  milestone rather than one isolated issue, create a milestone branch and implement each
  issue or declared batch on a separate feature branch. On each feature branch: implement
  and verify the complete issue/batch, commit it, and run `pdm run verify`. Do not open a
  feature PR and do not run the five-agent pre-PR review cycle there. Merge the verified
  feature branch into the milestone branch with `--no-ff`, then delete that feature branch
  locally and remotely. Repeat until every issue assigned to the milestone is implemented.
  A blocked issue blocks milestone completion unless its milestone assignment is explicitly
  changed; do not silently omit it.

  Only after every milestone issue is present on the milestone branch: run `pdm run verify`,
  commit any resulting fixes, and run the milestone branch's full five-agent pre-PR
  review/fix cycle repeatedly on committed milestone diffs until all five agents converge.
  Then rerun `pdm run verify`, run required branch
  CI/checks, and create the milestone's single PR. Wait for every triggered GitHub workflow.
  Merge the milestone PR only when the required target-branch and PR checks satisfy the hard
  merge rules above. After the merge, watch every post-merge workflow to completion before
  starting new work.
- Branches: `feat/<slug>-<issue#>`, `fix/...`, `security/...`, `docs/...`; PRs merge into
  `main`.
- **Dependabot PRs: fetch into exactly one ref name and delete it when the PR closes.** A
  previous session left 22 stray local branches by minting a new ref prefix per retry.
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
  be clean before review starts. Before PR creation, review the committed `main...HEAD`
  diff against current `main` across all five dimensions in the initial round:**
  1. **R1 correctness** — correctness, guideline compliance, security, and project rules
  2. **R2 silent failure** — swallowed errors and failures that look like clean results
  3. **R3 test validity** — do tests fail when production behavior is wrong, or agree with
     a fiction?
  4. **R4 comment accuracy** — do comments and docstrings claim guarantees not provided?
  5. **R5 type design** — are invariants enforced by types or caller convention?

  (`R<n>` are review dimensions; `D<n>` elsewhere names `docs/DECISIONS.md` entries.)
  Convergence is tracked **per dimension, not per tool or vendor agent**. Project-local
  OpenCode agents map to the dimensions as follows: R1 `pr-code-reviewer`, R2
  `pr-silent-failure-hunter`, R3 `pr-test-analyzer`, R4 `pr-comment-analyzer`, and R5
  `pr-type-design-analyzer`. Other harnesses may use different reviewers, but must preserve
  the five separate verdicts and all process rules below. Prefer a reviewer model family
  different from the implementer's where available.

  **Only the implementer makes lasting repository code, test, documentation, fix, or commit edits. R3 is the sole
  transient exception and runs ALONE (see D49).** R3 may mutate production code only to
  prove that a relevant test rejects wrong behavior. Before each mutation it copies the
  target outside the worktree (for example under `$TMPDIR/opencode/`, or another approved
  session temp path), then restores the original bytes byte-exactly from that external
  backup. It never fixes code, stages, commits, pushes, merges, rebases, stashes,
  resets, cleans, checks out, or uses Git to restore a target. Run the other non-converged
  dimensions in parallel, then R3 alone against the same commit. The orchestrator must
  verify `git status --porcelain` is empty and `git rev-parse HEAD` is unchanged before
  accepting R3; otherwise the pass is inconclusive and non-converged.

  Fix every verifiable actionable finding, including sensible suggestions that can be
  confirmed in the repo. Send lasting fixes to the implementer, re-run applicable gates,
  commit them, and repeat only the non-converged dimensions; R3 remains isolated. A
  dimension converges only after a successful full-diff review explicitly reports no
  unresolved actionable verified findings. Failed, timed-out, or inconclusive reviews do
  not converge. Once a dimension converges, do not run it again in that review cycle.

  Every round reviews a clean worktree and committed diff. Pushes and PR creation or updates
  remain manual user actions. The orchestrator may manage the issue and milestone lifecycle in
  `hniedner/ontoprism` through the repository-owned `pdm run agent-github` wrapper when the task
  explicitly requests it: create, edit, comment, label, assign or unassign, set or remove a
  milestone, close, or reopen issues; and create, edit, close, or reopen milestones. It never
  deletes issues or milestones or silently rewrites unrelated records. PR merge remains separately
  restricted to exact current-conversation authorization and the hard checks above.
  After all five dimensions
  converge, run final `pdm run verify`. Do not create a PR until convergence and final gates
  pass. Branch CI may be dispatched before a PR; CodeQL still requires its configured
  GitHub event. PR creation occurs only when requested. Run `gh pr merge` only when the user
  explicitly authorizes the exact PR in the current conversation and every hard merge check
  passes. This does not prohibit the milestone procedure's
  local `pdm run agent-git merge-no-ff <branch>` integration of a verified issue branch into its milestone
  branch. Record any genuinely unverifiable or unactionable exception and its reason.
- **Ephemeral planning/handover docs live in `tmp/plans/` (gitignored), never tracked.**
  Plan-mode plan files and any implementation handover written for a follow-up session go
  under `./tmp/plans/`, not in `.opencode/plans/` or `docs/`. Durable knowledge belongs in
  the tracked docs (`docs/DECISIONS.md`, `docs/design/`) and GitHub issues. Tracked product
  code, product documentation, and issues must not depend on ephemeral artifacts there;
  policy text and executable reproducibility commands may name the location to explain or
  enforce that rule.
