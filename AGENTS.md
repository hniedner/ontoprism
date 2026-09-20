# AGENTS.md

ONTOPRISM explores NCIt and caDSR and decomposes pre-coordinated NCIt concepts into
constituents on semantic axes. FastAPI backend over QLever/SPARQL and Postgres/pgvector,
SvelteKit 5 frontend, shared Python library `ontolib`. Product goals: `README.md`. Layout:
`docs/ARCHITECTURE.md`. Decisions and their reasons: `docs/DECISIONS.md` (D1, D2, ...).

These rules apply to every agent and harness (OpenCode, Claude Code, others).

## How work flows

> **Owner decision, 2026-09-20 (D92).** An issue branch merges into the milestone
> branch **locally, with no PR and no five-dimension review**. The milestone branch is
> **pushed immediately after each issue merge, and that CI run must go green before the
> next issue starts** — CI triggers on `push` to `main` and `feat/m[0-9]*`, so this is
> the per-issue gate that replaces the issue PR. The five-dimension review runs **once
> per milestone**, to convergence, on `git diff --no-ext-diff main...<milestone branch>`,
> before the milestone PR is opened. The reason is cost: a review round measured ~145k
> tokens per reviewer and ~725k per full round on #389, and ran to eight rounds on one
> issue. This supersedes D89. The rest of this file is written to match; #338 shortens
> it.

Work is organised in **milestones**. A milestone has a milestone branch off `main`
(`feat/m<number>-<slug>`). Each issue gets an issue branch forked from the milestone
branch and merges back into it. When every issue of the milestone is merged,
the milestone branch gets its own PR to `main`. A change that belongs to no milestone
(a hotfix, a dependency bump) does **not** use steps 8-10: it is never merged locally
into `main`. It follows steps 1-7 on its own branch, then takes a PR into `main`,
reviewed in all five dimensions.

For each issue:

1. Read the issue. Its body is the contract. If it is unclear or too large, say so and
   propose a smaller one before writing code.
2. Branch from the current milestone branch: `feat/<slug>-<issue#>`, `fix/...`,
   `docs/...`, `chore/...`.
3. Write a failing behavioural test, run it, and see it fail for the intended reason.
4. Make it pass with the least code. Refactor with tests green.
5. Inner loop: run only the tests for the code you touched (seconds to two minutes).
6. Commit. Pre-commit runs on the commit.
7. Before merging, run `pdm run lint` and `pdm run verify` once. If `.opencode/agent/*.md`
   changed, also run the permission contract against the real OpenCode binary; a skip is
   not a pass.
8. **Merge the issue branch into the milestone branch locally — no PR, no five-dimension
   review** — and delete the issue branch. For OpenCode the merge is
   `pdm run agent-git merge-no-ff <branch>`, a **prompted** command: the owner approves
   it or does it (`git merge` is denied outright in the maps).
9. **Push the milestone branch immediately** and watch that CI run to completion
   (post-merge watch, below). **That run is the gate of record for the issue.** Never
   batch several issue merges before pushing.
10. If the run is red, stop: do not start the next issue. Fix the cause on a new issue
    branch and repeat from step 3.

For the milestone:

11. When all its issues are merged, bring current `main` into the milestone branch (a
    local merge is a prompted command for the agent, for OpenCode `pdm run agent-git
    merge-no-ff <branch>`, which merges the local branch, so update local `main` first
    with `git fetch origin main:main`, also prompted; the owner approves it or does it),
    run `pdm run verify` once, **run the five-dimension review to convergence on
    `git diff --no-ext-diff main...<milestone branch>`** (see Review), and then open the
    milestone PR to `main`. Only this PR's title reaches the release (see Conventions),
    so its type is the highest-impact type among the issue commit subjects on the branch
    (`feat` > `fix` or `perf` > any other), and a `!` on any of them carries over; a
    `BREAKING CHANGE:` footer never reaches the release, because the squash body is
    blank. Its body lists the issues the milestone landed, the five review verdicts,
    every dropped finding, every deferral (blockers first) and every pending edit to this
    milestone's description (see "What a finding becomes", step 5). This is the only
    review the milestone gets, so it reads the whole diff, not a sample.
12. Merge the milestone PR to `main` once the protocol is met; the owner's standing
    authorization covers it (see Hard rules, D91). After it, watch the CI run on `main`
    (post-merge watch, below) before starting the next milestone.

**Post-merge watch (steps 9 and 12).** Find the CI run for the merge commit. After a
local issue merge (step 9) the SHA is `git rev-parse HEAD` on the milestone branch once
the merge is pushed; after a PR merge (step 12) it is the full merge SHA that
`pdm run agent-github pr-merge` prints (`merge_commit`), or `gh pr view <n> --json
mergeCommit --jq .mergeCommit.oid`. Then poll `gh run list --workflow
CI --event push --commit <sha> --json databaseId,conclusion` up to ten times, about a
minute apart (the OpenCode primary waits with `sleep 60`; other harnesses use their own
bounded wait). A short SHA matches nothing; never take the newest run on the branch
instead. If no run appears by then, that is a failure. Watch the run with `gh run watch
<id> --exit-status`. A non-zero exit is a failure unless `gh run view <id> --json
conclusion` says `cancelled` and a newer push run exists on the branch
(`cancel-in-progress` cancels a run when another merge follows); then watch that newer
run, which tests the combined tree, and repeat. On a failure, stop: do not start the
next issue or milestone, report it to the owner, and fix the cause on a new issue branch
("What a finding becomes"). The agent does not judge whether a merge into `main`
produced a release: the `Release` guard can hand a release to a newer merge, so that
cannot be read reliably from outside `release.yml`, and making a lost release visible is
tracked in #131.

Three rules keep this model from stalling, as it did in September when a milestone branch
grew to 94k unreviewed lines with no CI run:

- **CI must run on the milestone branch, per issue.** Push the milestone branch
  immediately after each issue merge and watch that run to completion. Never batch
  several issue merges before pushing, and never start the next issue while that run is
  red. If a push produces no CI run, stop and fix the workflow triggers first. This is
  what replaces the issue PR, and it is the rule that keeps the September failure from
  recurring.
- **Review happens once per milestone, on the whole diff against `main`**, before the
  milestone PR is opened.
- **A stalled milestone is split, not extended.** If the remaining issues are blocked or
  have grown, move them to a follow-on milestone and land what is finished. Ask the owner
  to confirm the split.

One session may carry a whole milestone. Start a new one when context is exhausted or
the work changes character; do not carry one context across days of work.

## Hard rules

- **Never commit to `main`.** Everything reaches `main` through a PR. `main` is protected: no
  force-push, no deletion.
- **Merge authorization is standing and contingent on the protocol.** The owner has
  authorized agents (2026-09-19, D91) to merge a PR into `main` without asking per PR,
  and only when both hold. First, the branch was vetted, tested and reviewed under the
  protocol: for a change that belongs to no milestone, steps 1-7 and a PR into `main`
  reviewed in all five dimensions; for a milestone PR, step 11 after every issue met
  steps 1-10. That includes `pdm run verify` before the PR was opened, all five review
  dimensions converged on the milestone diff, and a body as step 11 describes. Second, every expected check passes under the
  check rule below. If either fails, do not merge. GitHub does not yet enforce
  required checks on `main` (#405), so keeping the check rule is the agent's job.
- **Never merge any PR, into `main` or a milestone branch, unless every expected check
  in `gh pr checks <n>` is present and passing for the current head and base (or skipped
  by a documented path filter); a check that is absent, or a run made before the PR's
  base was changed, does not count, so get a fresh run first.**
  A base change means the PR was retargeted to another branch: record the base (`gh pr
  view <n> --json baseRefName`) when the review converges and compare it just before
  merging; after a retarget, push a new commit (a re-run keeps the old base) before
  counting checks. New commits on the base branch, such as the release and README bot
  commits on `main`, do not void a run; a merge skew between two PRs shows up in the CI
  run on the base branch after the merge, which steps 9 and 12 watch, and whether
  GitHub should require up-to-date branches is decided in #405. Ignore the rows whose
  event is `push` (`gh pr checks <n> --json name,event,bucket`): a PR whose head is a
  milestone branch also shows push rows under the same names, while CodeQL's aggregate
  row has an empty event and its `Analyze` jobs show `dynamic`. On every PR, `CI
  summary`, `quality (pre-commit parity)`, `conventional commit subject` and `dependency
  review` are always expected; on a PR into `main`, so are `CodeQL` and its `Analyze`
  jobs, except under the known quirk below. If one is missing, CI did not run fully.
  Merge only with `pdm run agent-github pr-merge <n> --head <sha> --base <branch>`,
  where `<sha>` is the full reviewed head SHA from `git rev-parse` and `<branch>` the
  base recorded when the review converged. The wrapper refuses a PR that is not open,
  comes from another repository, or whose head or base differ; squash-merges pinned to
  `<sha>` (GitHub refuses the merge if the head moved) with the subject `<PR title>
  (#<n>)`, which keeps the PR number in the log, and an empty body; and prints the merge
  commit for the post-merge watch (after a merge whose branch deletion failed, the merge
  commit is in the error message). The head branch is left to GitHub while the
  repository's `delete_branch_on_merge` setting is on (the wrapper does not confirm the
  deletion), and deleted by the wrapper otherwise. Never run `gh pr merge` in any
  harness; the OpenCode maps deny it.
  Never `--admin`, auto-merge, or a queue. Re-read the PR just before merging; if its
  head, title or base changed after the checks and the review, they run again first.
  Known quirk: PRs touching only dependency manifests or workflows show the aggregate
  `CodeQL` check as neutral with no `Analyze` jobs; that is expected for those PRs only.
  CodeQL runs on `main` and on PRs into `main`, not on PRs into a milestone branch, so a
  milestone PR is the first place it reports on the milestone's code.
- **No dead code and no legacy compatibility code.** The product is pre-production:
  rebuild internal data instead of keeping old-schema readers, adapters or fallbacks.
- **Never signal a process you did not start, and never choose one by port or by name.**
  No `lsof -ti :PORT | xargs kill`, no sweep over a port range, no `pkill`, `killall` or
  `fuser -k`. Record the pid you launched, together with its start time so a reused pid
  is not mistaken for it, and signal that; a port held by anything else is reported by
  pid, never signalled, and a lookup that failed is never read as "the port is free".
  The rule holds inside scripts and tools this repository ships, not only at an agent's
  prompt: on 2026-09-18 another project's port sweep killed the Podman VM's `gvproxy`
  (docs/DATA_SETUP.md), and the guard against it typed by hand is a local hook on the
  owner's machine, which sees nothing a script does. `scripts/dev/servers.py` is the worked
  example, pinned by `backend/tests/test_dev_script.py`, which also refuses the usual
  kill-by-port and kill-by-name spellings anywhere under `scripts/`.
- **Destructive or irreversible actions need the owner's go-ahead**: deleting data or
  volumes, resetting the Podman VM, overwriting a run artifact. Write new outputs to new
  paths; never overwrite an artifact another step may still need.

## Scope discipline

These rules exist because the project lost weeks to work that proved things about itself
instead of improving the product.

- **The issue body is the only place acceptance criteria live, and only the owner changes
  them.** Do not post "amendment" comments that alter scope. If you think the criteria
  are wrong, stop and ask.
- **Newly discovered work never widens the current issue's acceptance criteria.** What it
  becomes (a fix in the same PR, a comment on an existing issue, a new issue) is decided
  by "What a finding becomes" under Review. Ask before treating it as a prerequisite.
- **Do not build machinery to certify your own work.** No content hashes of source files,
  git HEAD, or worktree state inside data or evidence files. No committed derived files
  that must be regenerated after an unrelated edit. No tests that assert the wording of
  documentation. No identity chains between intermediate files. If you need to know
  whether something is current, recompute it.
- **Acceptance is never "every item across the whole corpus is classified".** Work on a
  sample, measure, improve, widen the sample.
- **Stop at twice the estimate.** If a task has taken twice what you expected, stop,
  report what you learned, and propose a simpler route. Do the same after a second failed
  attempt at the same step.
- **Prefer deleting complexity to adding a rule about it.** If a rule in this file has
  failed twice, remove whatever made the rule necessary.
- Issues written before 2026-09-17 may demand identity binding, hash evidence, or
  reject-branch liveness for every gate. Those demands are void (review to convergence
  is not one of them; it stands). Before starting such an issue, rewrite it as Why /
  Scope / Done when (about five checkable criteria) and have the owner confirm.

## Say what you observed, not what you assume

- A factual claim about state (a count, a status, a digest, "tests pass", "file exists")
  comes from a command you ran in this session. Otherwise say "not verified".
- Never restate a number or hash from memory or from an earlier document.
- `tmp/` is gitignored: search it with `rg --no-ignore` (the OpenCode primary has no
  `rg`; read the path directly), or a present file looks absent.
- A plan step names its inputs, and you have checked each input exists.
- Dry-run the downstream path before asking a person for a sign-off.

## Testing

Quality goals are unchanged: strict TDD, behavioural tests, real-boundary contracts,
aggregate coverage above 90%. What changed is *when* each lane runs.

| When | What | Command |
|---|---|---|
| Inner loop | the tests for what you touched | `pdm run agent-test <path>[::test] -v` |
| Before commit, if the change is broad | hermetic unit lane (about 4.5 minutes, measured 2026-09-17) | `pdm run test-unit` |
| On commit | pre-commit hooks | automatic |
| Before merging an issue, once | lint, then everything CI runs | `pdm run lint`, `pdm run verify` |
| Gate of record, per issue | CI on the pushed milestone branch | `gh run list --workflow CI --event push --commit <sha>` |
| Gate of record, per milestone | CI and CodeQL on the milestone PR | `gh pr checks <n>` |
| After editing `.opencode/agent/*.md` | contract against the real OpenCode binary; a skip is not a pass. The binary is auto-discovered on the owner's machine; elsewhere the owner exports `ONTOPRISM_OPENCODE_BIN` before launching (an inline prefix is prompted or denied for an agent) | `pdm run agent-test backend/tests/test_agent_permission_safety.py` |
| When the change touches a real store contract | read-only contracts on configured corpora | `pdm run agent-test --full-store <node> -v` |

Other lanes: `pdm run test` (grouped hermetic suites), `pdm run test-integration`
(disposable Postgres/QLever, needs Podman), `pdm run test-ci` (the strict coverage gate),
`pdm run agent-test --safe-integration <node>`, `pdm run agent-test --frontend <file>`.
Use `pdm run agent-test`, not `python -m pytest`: the module form puts the repo root on
`sys.path`, where the outer `ontolib/` and `backend/` directories shadow the editable
installs (D6).

A failing `verify` is information, not a ritual: fix the cause, rerun the failing lane,
and rerun full `verify` once at the end. Do not run `verify` after every edit.

Rules for tests:

- **TDD means an observed RED.** Run the new test before the production edit and confirm
  it fails for the intended reason. A suite that collects zero tests is a failure.
- **Every test is a regression indicator.** If a relevant wrong behaviour would not make
  it fail, delete or replace it. No execution-only tests, mock choreography, fakes that
  clone the implementation, fixture self-consistency, or assertions added to satisfy a
  coverage or quality tool.
- **Coverage above 90% line and branch is an aggregate floor** for `ontolib/src`,
  `backend/src` and `frontend/src/lib`, enforced in CI. It is a by-product of testing
  behaviour. There is no per-function gate. Do not lower a gate; raise real coverage or
  record a justified exception in the PR.
- **External boundaries need more than doubles.** On #73 about twelve bugs passed a green
  TDD suite; none was a logic error. Each was a false belief about ROBOT, ELK, asyncpg,
  RDF serialization or the real Uberon data, shared by the code and its hand-made double.
  So when code depends on an external tool, driver, service or upstream dataset, add:
  a **contract test** of what the tool itself does; a **double-fidelity test** running
  the same input through the double and the real thing; a **data-shape test** pinning what
  the real store looks like (these run locally against configured stores and skip in CI
  by design; a skip is not a pass); and a **liveness test** showing the reject branch of
  a gate can fire on production-shaped input.
  "External" means something we do not control. Files our own pipeline wrote are not an
  external boundary and do not get this treatment.
- Mutating integration tests use nonce-owned disposable fixtures, are listed in
  `test_support/integration_mutators.toml`, and never touch `live_api_client`, `ncit_url`
  or a configured persistent store. A required disposable service that fails to start is
  a failure, not a skip.
- Frontend: fire-and-forget rejections inside a Svelte `$effect` trip vitest's unhandled-
  rejection guard when a mock is reset between tests. Use `mockClear`, not `mockReset`.

## Long-running jobs

A full-corpus decompose takes about 15 hours (runs of 891 and 934 minutes in September
2026). That month most full-run attempts were lost to short timeouts and to errors a
five-minute sample would have shown (owner's session records, 2026-09-17).

- Run the whole pipeline, including final reporting, on a small sample first.
- Validate every input before the expensive step, not after it.
- Set the tool timeout to at least 1.5 times the expected duration, or run the job in
  the background and poll. Use resume where it exists.
- A long run is never preconditioned on a commit or a clean worktree.
- Engine changes are judged on the 20-concept SME oracle (D63), not on full runs. Schedule
  a full run after several fixes have accumulated, with the owner's agreement.
- Keep completed full-run artifacts. Never write over one.

## Domain principles

**Everything ONTOPRISM emits is NCIt** (D60, qualified by D86): NCIt reorganised, not NCIt
blended with other ontologies. A concept or role we introduce is enhanced-NCIt content
even when it matches or was derived from Uberon, CL, SNOMED CT or ICD-O-3. Derivation is
recorded as provenance and alignment, never ownership: `op:PrimarySite` is our relation
and `RO:0004026` is what it aligns to. Write "derived from", "aligned to", "corroborated
by", "proposed, evidenced by"; never "external content", "borrowed from", "depends on".
Lifecycle: `proposed -> locally-approved -> submitted -> accepted-in-ncit`. Locally
approved means our SME accepted it, not NCI. Official source axioms are never deleted; a
correction is a separately identified effective view with a visible delta.

**Evidence policy (owner, 2026-09-17).** Decomposition decisions are backed by expert-
curated sources and peer-reviewed literature, with the source evidence preserved and
linked to the individual decision, and the result is checked with a description-logic
reasoner. A human SME resolves only what has no evidence, contradictory evidence, or
ambiguity that context and the reasoner cannot resolve. Evidence is product data stored
per assertion and shown in the UI. It is not a release gate. The engine's own provenance
fields are not evidence. Published engine output is labelled `provisional` until an
assertion has evidence (milestone M1.8).

**Decomposition is additive.** Legacy pre-coordinated concepts are flagged
(`representationStatus="legacy-precoordinated"`), never deleted; decomposed triples go to
the separate `ncit_decomposed` graph. Extraction reads the **stated** OWL, not the
inferred store. Exact reversibility stays quarantined until there is a proof-bearing
representation (D43). Variant links are navigation between distinct concepts, never
equivalence (D39).

## Architecture notes not obvious from the code

- NCIt roles are OWL existential restrictions
  (`?c rdfs:subClassOf [owl:onProperty ?R; owl:someValuesFrom ?filler]`), not direct
  triples; the restriction-traversal query in `ontolib/src/ontolib/terminologies/ncit/`
  makes them queryable. Associations are direct triples.
- The frontend talks only to the FastAPI backend. The backend owns all QLever and
  Postgres access.
- The stated OWL is stream-converted by pinned Jena RIOT and indexed by QLever's offline
  builder; it is never uploaded over HTTP.
- `pdm run data-build` (owl -> cadsr -> embeddings) rebuilds all data from public
  sources. The embeddings step needs `pdm install -G data-build`.
- Validate user input separately from source data: only input errors become 4xx;
  malformed source rows fail closed.

## Repo layout

- `ontolib/` - shared library (storage, terminologies, repositories, decomposition engine)
- `backend/` - FastAPI app (`backend/src/backend/main.py`, routers under `api/`)
- `frontend/` - SvelteKit 5 app, its own npm project
- Root `pyproject.toml` holds every `pdm run` script and the ruff, basedpyright, coverage
  and pytest configuration. Run everything from the repo root.

## Setup

```bash
pdm install --dev            # Python 3.14.7
npm ci --prefix frontend
cp .env.example .env
pdm run python scripts/install_jena.py --install-dir "$PWD/.tools/jena-6.1.0"
pdm run python scripts/install_robot.py --install-dir "$PWD/.tools/robot-1.9.10"
export ONTOPRISM_JENA_DIR="$PWD/.tools/jena-6.1.0" ONTOPRISM_ROBOT_DIR="$PWD/.tools/robot-1.9.10"
pdm run data-build owl && pdm run data-build ncit-bootstrap && pdm run data-build uberon-store
pdm run agent-replay ensure-podman-stack   # select/recover the Podman VM and the data stack
pdm run up && pdm run migrate              # `migrate-stamp` on a pre-existing cloned DB
pdm run start-all                          # backend :8011, frontend :5175, logs in .dev-logs/
```

Ports are offset from the sibling `fairdata` app so both can run (`docs/DATA_SETUP.md`).
Agents may run `pdm run agent-replay ensure-podman-stack` without asking; it performs one
normal stop/start of `ontoprism-vm` and reconciles the three-service stack. It never
authorizes VM reset or removal, volume deletion, or free-form `podman machine` commands.
If it fails, report it; do not work around it.

Lint and format: `pdm run lint` (ruff + basedpyright), `pdm run fmt`. Frontend, from
`frontend/`: `npx eslint src/ --max-warnings=0`, `npm run check`, `npm run fallow`. The
fallow gate reports only findings new against the PR base: in CI that is the real base;
locally it is `origin/main` unless `FALLOW_BASE=<milestone branch>` is set (for an agent
that prefix is a prompted command), so a local run on an issue branch may show findings
from sibling issues already merged into the milestone branch. The same holds for the
per-issue gate itself: a `push` run sets no `GITHUB_BASE_REF` and no `FALLOW_BASE`, so
fallow compares against `origin/main` and audits everything the milestone has
accumulated, not only the issue you merged. It blocks rather than warns, so keep the
whole milestone diff fallow-clean.
Workflows stay SHA-pinned and Docker base images digest-pinned (`zizmor` hook, D30/D31).

## Review

Review runs **once per milestone** (owner decision, 2026-09-20): before the milestone
PR is opened, review `git diff --no-ext-diff main...<milestone branch>` in
**all five dimensions** (round 1 is
all five; later rounds re-run only what has not converged — see "Which dimensions run
in which round"). The owner's account of the #73 review is that each dimension caught a
class of defect the others missed; that is why round 1 with a subset is never acceptable:

1. **Correctness and project rules** (`pr-code-reviewer`)
2. **Silent failures**: swallowed errors, failures that look like clean results
   (`pr-silent-failure-hunter`)
3. **Test validity**: does a changed test fail when production behaviour is wrong?
   (`pr-test-analyzer`; runs alone because it temporarily mutates production code, restores
   the original bytes from a copy outside the worktree, and must leave
   `git status --porcelain` empty)
4. **Comment accuracy**: comments and docstrings that promise more than the code delivers
   (`pr-comment-analyzer`)
5. **Type design**: invariants left to caller convention (`pr-type-design-analyzer`)

Run 1, 2, 4 and 5 in parallel, then 3 alone. **Only dimension 3 may modify tracked
files**, and only as its own mutations, restored from a copy kept outside the worktree.
Never authorize another dimension to mutate: a brief that let one mutate while the
read-only dimensions were running gave two of them phantom test failures they had to
recognise and discount (#389, 2026-09-20). A missing, timed-out or inconclusive verdict
is a non-converged dimension, not a clean one. Other harnesses use their own reviewers
but keep the five separate verdicts.

**Review runs to convergence.** Address every verified finding and every reasonable
suggestion in the PR; the only exception is a major out-of-scope finding (see "What a
finding becomes"). A dimension has converged when a full pass reports no unresolved
verified finding and its suggestions are addressed; a deferred finding counts as
resolved only once the PR body lists it (step 3 below). There is no round ceiling, and
an existing PR is never rejected as too big.

**Which dimensions run in which round.** This is a rule about cost, not taste: a
reviewer agent costs roughly 145k tokens and a full round of five roughly 725k
(measured on #389, 2026-09-20). Re-running a dimension that has already converged buys
nothing and is the main way a PR's review bill multiplies.

- **Round 1: all five.** Always. 1, 2, 4 and 5 in parallel, then 3 alone.
- **Every later round: only the dimensions that have not converged.** Never re-run a
  converged dimension for reassurance, for completeness, or because the diff "feels"
  different. If it converged, it is done.
- **A converged dimension re-arms only when the fix range contains the kind of thing it
  reviews**, and you name that thing when you re-run it: a changed or added test
  re-arms test validity; a changed comment or docstring re-arms comment accuracy; a new
  or changed error path re-arms silent failures; a new type, signature or invariant
  re-arms type design; changed production logic re-arms correctness. A fix that only
  reworded a message does not re-arm type design.
- **Replacing the implementation re-arms everything**, because nothing the earlier
  rounds reviewed still exists. This is one more reason to settle the approach before
  the first review round rather than after it.
- **Brief every re-run** with its own previous findings and what was done about each, and
  point it at the fix range, not the whole diff.

Size is decided when the work is planned: one issue or one coherent change per issue
branch. Review cost no longer scales with issue granularity, because the review runs once
per milestone; what each issue still costs is a CI run on the milestone branch (about
seven minutes). Split at planning
time, not at review time.

### What a finding becomes

Every real finding is fixed, however small: minor defects compound (owner, 2026-09-19).
There is no severity threshold. A fix is recorded by its commit; a dropped or deferred
finding is recorded in the PR body. What keeps the tracker finite is that a finding is
real, is fixed where it was found, and is filed at most once:

1. **Verify it first.** A finding counts when it is reproduced, or shown in the code
   together with the input or state that triggers it. One that cannot be verified is
   dropped, with a one-line reason in the PR body. Never file, fix or "harden against" an
   unverified finding.
2. **Fix it where it was found**: on the issue branch that surfaced it, including for
   findings in code it only touches in passing. A finding from the milestone review is
   fixed on a new issue branch merged into the milestone branch, never as a direct commit
   to it; its commit references the issue whose change the finding concerns.
3. **Defer only a major, out-of-scope finding** (a blocker included): one whose fix needs
   its own design, its own tests and its own review, and does not belong to the issue's
   contract. Size alone is not a reason, and neither is inconvenience. List every
   deferral in the PR body with its issue number (for a finding added to an existing
   issue, the URL of the comment) and the sentence of the issue body that puts it out of
   scope. A deferral missing from that list is unresolved.
4. **Search before filing.** Read the open issues first. If one covers the same cause or
   the same code area, add the finding to that issue (a comment that records the
   finding; it changes no acceptance criterion until the owner folds it into the body).
   Earlier findings are parked the same way, so read an issue's comments, not only its
   body, before ruling it out. Findings of one review that share a cause or a code
   area become one issue, not one each.
5. **Place it.** A new issue gets a milestone and a position in that milestone's order;
   the order lives in the milestone description, and in a milestone without one the
   position is stated by dependency (what the issue blocks, what blocks it). Writing the
   position into the milestone description is a milestone edit the owner confirms, so
   until then the new issue's body states the proposed position and says it is pending;
   the note is removed when the owner decides. The milestone PR of the milestone being
   edited lists every such pending edit. "No milestone" is for
   epics and for collected minor work that blocks nothing.
6. **If placing it shows the milestones no longer fit** (a milestone's goal depends on
   work planned later, or a milestone has grown past what can land), propose the
   reorganization to the owner with the reason. Moving issues between milestones,
   reordering a milestone or changing its goal needs the owner's confirmation.

The `issue-steward` agent judges a finding by steps 1-6 on request and returns a verdict
per finding (OpenCode: `.opencode/agent/issue-steward.md`; Claude Code:
`.claude/agents/issue-steward.md`). It is read-only: the engineer makes the fix, writes
the tracker and asks the owner. Use it when a review produced a finding you want to
defer, or when the owner asks for a tracker pass; a finding you simply fix needs no
steward.

## Conventions

- PR titles are Conventional Commits; CI enforces it. Releases derive versions from the
  squash commit on `main` (`feat` -> minor, `fix`/`perf` -> patch; pre-1.0, see D18):
  its subject is the PR title with ` (#<n>)` appended, and its body is empty because the
  repository's squash message is `BLANK` (D91). A merge body would be parsed too;
  `pdm run agent-github pr-merge` passes none, and no agent map allows `gh pr merge`.
  Issue commit subjects inside a milestone do not reach the release; the
  milestone PR's title type does (step 11).
- `Closes #X` only when the PR fully resolves the issue; never on an `epic` issue (D35).
- Do not hand-edit `CHANGELOG.md` or version numbers; semantic-release owns both.
- Dependabot PRs: fetch into one ref name and delete it when the PR closes.
- Planning and handover notes go in `tmp/plans/` (gitignored). Tracked code, docs and
  issues never depend on files there. Durable knowledge goes in `docs/` or an issue.
- A scratch script for diagnostics goes in `tmp/scratch/` and is run with
  `pdm run python tmp/scratch/<name>.py`. Scratch scripts read; they do not modify repo
  data or stores. Do not turn a one-off diagnostic into tested production tooling.
