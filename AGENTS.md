# AGENTS.md

ONTOPRISM explores NCIt and caDSR and decomposes pre-coordinated NCIt concepts into
constituents on semantic axes. The FastAPI backend uses QLever and Postgres/pgvector;
the SvelteKit frontend talks only to that backend; `ontolib` is the shared Python
library. Product goals: `README.md`. Architecture: `docs/ARCHITECTURE.md`. Decisions:
`docs/DECISIONS.md`.

These rules bind every agent and harness. The issue body is the contract; only the
owner changes it.

## Current scope and expert gate (D96 amended by D98)

The product is an **NCIt Neoplasm decomposition demonstrator with expert review**.
Ontology-generic platform work, NAACCR, vision stages 3–5, post-coordination grammar,
cloud, promotion of mappings to identity grade, and literature generation are scheduled
after G1 and start only after a "go" from it. Two things are not parked: correcting the
existing cross-references (#226, #151, #159, #120), and keeping the repositories current
(#490 to #494). A standard backend library and component set reused by the shipped
repositories (#488) does not unpark the ontology-generic platform. Follow the
owner-approved numbered delivery milestones (M10 onward) in due-date order: each states
its value and issue order, contains at most five issues and ends with one reviewed PR to
main. Split a stalled milestone rather than extending it. The static #461 packet is a
development instrument, not G1. No new source release (NCIt, caDSR, Uberon, ICD-O) is
activated on the instance until enhancements can be carried forward (#492 to #494).

Evidence precedes human review: tier 0 stated NCIt axioms; tier 1 expert-curated
ontologies (Uberon, NCIt P334 / ICD-O-3, SNOMED MRCM, mCODE); tier 2 a DL reasoner;
tier 3 peer-reviewed literature with verified quotes. Tiers 0–2 run on each assertion
before it reaches a human. Propose per-axis cardinality, morphology and residual-metric
semantics from curated sources for the owner's approval in their issues. Conflicting or
unresolved assertions go to the `needs-SME` queue. Evidence milestones report the
evidence-supported share and queue size by reason and axis, before and after.

Experts work the `needs-SME` queue in the completed graph-based curation interface at
independent gate G1; the owner records their decisions and go/no-go. If no independent
expert commits to sessions by **2026-11-06**, stop platform work and write up the
findings (told closure, P334 histology anchoring, detector saturation, D58 cases) for
NCI EVS. G1 no longer precedes the owner-approved corrected full run (#470).

## Workflow

Work is organised in milestones. A milestone branch (`feat/m<number>-<slug>`) starts
from `main`. Each issue has one coherent issue branch from the current milestone
branch. A hotfix or dependency change outside a milestone follows the same quality
steps but reaches `main` through its own reviewed PR; it is never merged locally.

### Issue loop

1. Read the issue and its comments. If the body is ambiguous, too large, or uses the
   pre-2026-09-17 identity/hash/every-gate-liveness style, propose a plain Why / Scope /
   Done when rewrite and wait for the owner.
2. Update and check out the milestone branch, then create `feat/<slug>-<issue#>`,
   `fix/...`, `docs/...` or `chore/...` from it.
3. TDD: write a behavioural test, run that exact test, observe RED for the intended
   reason, make the least change to GREEN, then refactor with targeted tests green.
4. Commit. Pre-commit runs automatically. Before merging, run `pdm run lint` as a fast
   fail and `pdm run verify` once. If `.opencode/agent/*.md` changed, also run
   `pdm run agent-test tooling_tests/test_agent_permission_safety.py`; a skip is not a
   pass.
5. Check out the milestone branch, merge locally with
   `pdm run agent-git merge-no-ff <issue-branch>`, then delete the branch with
   `pdm run agent-git delete-merged <issue-branch>`. Issue branches get no PR and no
   five-dimension review. The wrapper refuses to merge while HEAD is `main`, `master`
   or detached; bare `git merge` remains forbidden.
6. An issue gets no CI run of its own (D100). Its gate is step 4 (`pdm run lint`,
   `pdm run verify`) and its posted demo. Do not start the next issue before the
   previous one is merged. Pushing the milestone branch is optional, as a backup; it
   triggers no CI. A risky change may use a manually dispatched run
   (`pdm run agent-github workflow-dispatch --ref <branch>`). The issue stays open until
   the milestone PR merges; that PR closes it.

One session may carry a milestone; start another when context is exhausted or the work
changes character. A stalled milestone is split, not extended; ask the owner before
moving or reordering issues.

### Milestone completion

1. Update local `main`, merge it into the milestone branch, and run `pdm run verify`.
   Run `pdm run smoke-real` against the configured stores. It is read-only and starts
   and stops only the servers it launched. A skip or a known failure is not a pass:
   fix a red smoke or stop and report.
2. With the milestone branch checked out, review `git diff --no-ext-diff main...HEAD`
   in all five dimensions to convergence (Review below).
3. Open one milestone PR to `main`. Its title uses the highest-impact issue commit type
   (`feat` > `fix`/`perf` > others; preserve `!`). Its body lists landed issues with a
   link to each issue's demo result comment and a `Closes #n` line for each landed
   issue, all five verdicts, every dropped finding
   with its reason, every deferred finding with its issue, every red CI run restored
   without prior approval, and every pending milestone edit.
   The milestone branch is pushed here; the PR run is its first CI run.
4. Merge only after all expected checks pass, then watch CI on the exact merge SHA.
   When it is green, check out `main`, pull, and delete the milestone branch with
   `pdm run agent-git delete-merged`.

For the merge SHA use the `merge_commit` printed by `pdm run agent-github pr-merge`. Poll
`gh run list --workflow CI --event push --commit <sha> --json databaseId,conclusion`
up to ten times about a minute apart, then run `gh run watch <id> --exit-status`. A
short SHA or the newest branch run is not evidence. If no run appears or it fails,
stop and report. A cancelled run counts only when a newer push run on the branch exists
and passes. Whether a `main` merge released is not judged here (#131).

## Hard rules

- Never commit or push directly to `main`, force-push, delete it, or bypass the merge
  protocol. Destructive or irreversible actions (data/volume deletion, VM reset,
  overwriting completed artifacts) require the owner's approval.
- PR merge authorization is standing but contingent (D91): the applicable workflow,
  `pdm run verify`, review, PR body and every expected current-head/current-base check
  must be complete. Merge only with
  `pdm run agent-github pr-merge <n> --head <40-hex-sha> --base main`. Never use
  `gh pr merge`, `--admin`, auto-merge or a queue.
- Before merging a PR, re-read its head, title and base. Ignore `push` rows in
  `gh pr checks <n> --json name,event,bucket`. Every PR expects `CI summary`,
  `quality (pre-commit parity)`, `conventional commit subject` and `dependency review`.
  A PR to `main` also expects CodeQL and Analyze jobs. For dependency/workflow-only PRs,
  neutral aggregate CodeQL with no Analyze jobs is the documented exception. GitHub's
  `main integrity` ruleset enforces the five aggregate checks; it does not require the
  branch to be up to date, matching the base-skew rule above.
- No dead code or internal legacy compatibility. The product is pre-production: rebuild
  internal data rather than preserving old-schema readers or fallbacks.
- Never signal a process you did not start or select one by port/name. Record the PID
  and start time you launched; signal only that process group. Never use port sweeps,
  `pkill`, `killall` or `fuser -k`. This rule also binds repository scripts.
- Do not build machinery that certifies your own output: no source/git/worktree hashes
  in evidence, committed derived files regenerated by unrelated edits, documentation-
  wording tests, or identity chains between internal intermediates. Recompute instead.
- Newly found work does not widen an issue. Stop at twice the estimate, after the same
  step fails twice for the same reason, before widening acceptance, before
  self-certifying tooling, or before a destructive action. A bug in your own scratch
  diagnostic is not a failed step: fix it and continue. "The same reason" means no
  progress: a retry that moves a gate closer, with the remaining cause known, is not a
  repeat failure (the other stops still apply).

## Issue size, demos and approvals (D95)

- An issue changes at most about 400 net lines of non-test code. Going over: stop and ask
  the owner to split the issue. A split never separates code from the tests that
  motivated it. Tests normally stay within 1.5 times the code; more is allowed without
  stopping when every test covers a behaviour named in the issue's Done when or in an
  owner-approved policy. List that mapping in the demo result; the milestone review's
  test-validity dimension checks it for padding.
- New persistence needs the owner's approval, written in the issue, before work starts:
  a migration, a table, a persisted file format, or an identity/hash field. Announce a
  migration of the configured Postgres before running it.
- Every issue body has an estimate (so the twice-the-estimate stop can fire) and a demo in
  its Done when: a screenshot or Playwright flow for GUI work; before/after
  `pdm run oracle-metrics` output for engine work; for other work, the observable
  before/after the Done when names. A missing estimate or demo is not itself a reason to
  stop: before starting, post your own estimate (time and expected net non-test lines)
  and demo plan as an issue comment and proceed; if you expect the issue to exceed the
  size cap, stop and ask the owner to split it. Before the local merge, post the demo result (the before/after itself, not the
  plan) as an issue comment; the milestone PR body links each issue's demo result. No
  demo, no merge.
- Two corrective owner comments on one issue: stop and ask the owner to split it.
- No process, harness, CI or agent-configuration work unless something is actually
  blocked, and only with the owner's approval before it starts. A red gate-of-record CI
  run counts as blocked: restoring it without weakening any gate needs no prior approval
  and is reported in the milestone PR body.
- New issues come from two sources only: an owner-approved split, or an exceptional
  deferral under "Review". A finding made while implementing an issue is fixed in that
  issue when the owner allows it there; otherwise it is reported on the issue and fixed
  at the milestone review. Unverified suggestions are dropped with a reason, never filed.

## Evidence and diagnostics

State facts only from commands run in this session; otherwise say `not verified`.
`tmp/` is gitignored. Put read-only diagnostics in `tmp/scratch/` and run them with
`pdm run python`, `python` or `python3`; `jq .`, `sqlite3 -readonly -json`, and the fixed
`tmp/scratch/inspect.py | jq .` pipeline are also available there. Never read
credentials/home files or modify stores, repository data or artifacts. A plan names
checked inputs. Dry-run downstream paths before requesting sign-off.

Clean up what you created under `tmp/` once it is obsolete and you are sure: scratch
scripts when their issue is merged into the milestone branch, demo and PR-body files
when their comment is posted and the milestone PR is merged, screenshots when they are
attached or replaced, and temporary worktrees and remote branches when their work is
merged. Keep what an open issue, the current milestone or a tracked test still reads,
and anything that is human review work or source data. If unsure, keep it and ask. Do
this at each milestone completion, after the merge to `main` is green.

## Testing tiers

Quality stays strict; only timing is tiered:

| When | Gate |
|---|---|
| Inner loop | `pdm run agent-test <path>[::test] -v` for touched behaviour |
| Broad change before commit | optional `pdm run test-unit` |
| Commit | pre-commit |
| Before issue merge | `pdm run lint`, then one `pdm run verify` |
| Per-issue record | `pdm run lint`, `pdm run verify` and the posted demo (no CI run) |
| Per-milestone record | CI and CodeQL on the milestone PR |
| Real-store contract changed | `pdm run agent-test --full-store <node> -v` |

Use `pdm run agent-test`, not `python -m pytest` (D6). A failing `verify` is
information: fix and rerun its failing lane, then run full `verify` once at the end.

- TDD requires an observed RED. Zero collected tests is failure.
- Every test must detect a relevant behavioural regression. No execution-only tests,
  mock choreography, implementation-cloning fakes, fixture self-consistency or coverage
  padding.
- Aim for 95% or more line and branch coverage through behavioural tests. The aggregate
  for `ontolib/src`, `backend/src` and `frontend/src/lib` must stay above 90%: a hard
  floor, set low so hard-to-test code never invites padding, not a target. Real
  coverage just above the floor beats padded 100%. At the floor, test real behaviour or
  delete a branch only after showing from the code that neither input nor an external
  failure (tool, driver, store, I/O) reaches it; handlers for external failures are
  reachable. Never pad or lower the gate.
- For an external tool, driver, service or upstream dataset, add the applicable real
  contract, double-fidelity, real-data-shape and reject-liveness tests. Configured-store
  shape tests skip in CI by design; a skip is not a pass. Our own pipeline outputs are
  not an external boundary.
- Mutating integration tests use nonce-owned disposable fixtures listed in
  `test_support/integration_mutators.toml`; never mutate configured persistent stores.
- In Svelte `$effect` rejection tests use `mockClear`, not `mockReset`.

## Long-running jobs

Look at corpus shape before building on a run's output. From #457 on, every engine
report gives both the official oracle score (flagged emissions unscoreable) and the
plain exact-pair score. Until #457 lands, `oracle-metrics` output (the official score)
is the oracle demo. Every engine change shows corpus shape on the seeded 1,000-concept
sample rehearsal (or on a stored full run). Demos show whole-run
distributions, not only one hand-picked concept. At the start of an engine milestone,
check the applicable decisions against the implementation. Profile before optimising.
A time breakdown must reconcile with the process's wall-clock time measured from outside
(`/usr/bin/time`); report any gap over 5% and what fills it. A measurement used to decide
an issue must exercise the code that issue names.
Cleanup in a runtime import path (engine, backend, `oracle-metrics`) is not deferred
as "when touched".

Preflight the whole path on a small sample; validate inputs before the expensive step;
set timeout to at least 1.5 times expected duration or run in the background and poll;
use resume; never require a commit/clean tree; never overwrite completed artifacts.
Judge engine changes on the 20-concept SME oracle (D63), not a full corpus. Schedule a
full run only after several fixes, with a written corpus-shape prediction and owner
agreement. Never substitute a full run or the static packet for independent expert
review at G1.

## Domain and architecture

- One implementation per concern. Repositories, the data table and other shared
  controls are single configurable libraries or components; differences between
  repositories are declared configuration, not code. Before writing new code, name
  the existing module, component or maintained external library you checked. A
  parallel implementation is a review finding.
- Everything emitted is NCIt reorganised or enhanced-NCIt, even when derived from or
  aligned to another source (D60/D86). Record derivation/provenance, not external
  ownership. Lifecycle: `proposed -> locally-approved -> submitted -> accepted-in-ncit`.
  Official source axioms remain; corrections are separate effective views.
- Evidence is expert-curated source or peer-reviewed literature linked per assertion,
  checked with a DL reasoner. Engine provenance is not evidence. Published output stays
  `provisional` until evidenced (M1.8).
- Decomposition is additive. Legacy concepts remain flagged; new triples use
  `ncit_decomposed`. Read stated OWL. Variant links are navigation, not equivalence.
- The enhanced NCIt is an expert-review demonstration, not a release (D93). Everything
  is published to that audience, each concept with its engine outcome and any review
  flags, each with a reason; nothing is withheld. A concept without a recorded outcome,
  or a flag without a reason, is a publication error, never a default. The demonstration
  marker, outcome and flags travel with every exported artifact, not only the UI.
  Unresolved is first-class: a concept not decomposed or a role not disambiguated is
  recorded, with its kind, rationale and the evidence examined, as an `unknown` outcome
  or a review flag; never silently dropped and never published as resolved.
- NCIt roles are OWL existential restrictions, not direct triples; associations are
  direct. The backend owns all QLever/Postgres access. Stated OWL is RIOT-converted and
  offline-indexed. Validate user input separately from malformed source rows.

## Review

Review runs once per milestone before its PR, or once for a change belonging to no
milestone before its PR. Each dimension runs as its own reviewer agent; the implementing
agent never writes a verdict itself, and the PR body identifies each reviewer run.
Round 1 runs all five: correctness (`pr-code-reviewer`), silent failures
(`pr-silent-failure-hunter`), comment accuracy (`pr-comment-analyzer`) and type design
(`pr-type-design-analyzer`) in parallel, then test validity (`pr-test-analyzer`) alone.
Only the test analyzer may mutate tracked files, using
`pdm run agent-pristine save|restore|discard`; restore is one-shot. A missing,
timed-out, dirty-tree or changed-HEAD result has not converged.

Fix every verified finding and reasonable suggestion. Later rounds run only dimensions
that have not converged, on the fix range, briefed with prior findings and outcomes. A
converged dimension re-arms only when a fix changes what it reviews; replacing the
implementation re-arms all. Rounds are counted per dimension. After a dimension's third
round, its remaining suggestions are dropped with a one-line PR-body reason each and count
as addressed. A verified finding (reproduced, or code plus a triggering input or state)
is never a suggestion, whatever the reviewer labels it, and always continues (D95).

Verify a finding by reproduction or code plus triggering input/state. Drop an
unverified claim with a one-line PR-body reason.
Once all milestone issues are implemented, fix pre-PR review findings and reasonable
suggestions directly on the milestone branch. Commit coherent fixes there, run targeted
tests and the required pre-PR gates, and rerun affected review dimensions until all five
converge. Do not create corrective issue branches for this cycle.
Review findings are fixed in the same PR; there is no hand-over to a later issue or
another developer. The only exception is a finding that is both unrelated to the
milestone's scope and too large to fix in it (it needs its own design, tests and
review). Such a finding is deferred to its own PR and must have a GitHub issue: search
open issues first and add to one that covers the cause; otherwise create one. List each
deferral in the PR body with the issue URL and why it is unrelated. Size or
inconvenience alone never justifies a deferral. Use `issue-steward` to check a proposed
deferral or for a tracker pass. New issue placement or milestone reorganisation requires
owner confirmation.

## Conventions

PR titles and commits use Conventional Commits. Only the milestone PR title reaches the
release; never edit versions or `CHANGELOG.md`. Use `Closes #X` only when fully resolving
a non-epic issue. Plans live in gitignored `tmp/plans/`; durable knowledge lives in
tracked docs or issues. Workflows and container images remain pinned.
