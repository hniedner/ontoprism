# CLAUDE.local.md — ontoprism project standards

Project-specific rules. Merge with the global `~/.claude/CLAUDE.md` and the pre-PR
protocol in project memory. These are enforced, not aspirational.

## Testing (non-negotiable)

**Strict TDD for all new implementations.** Write the RED test first (a real,
behavioral test that fails for the right reason), then the minimum code to make it
GREEN, then refactor with the tests green. No production code without a failing test
that motivated it. The RED step is an executed observation, not intent: run the exact
new test before production edits and confirm its failure names the missing/wrong
behavior. A test written after the implementation, or one that was never observed red,
does not satisfy TDD.

**Coverage: maintain > 90%** (line and branch) across the backend (`ontolib/src`,
`backend/src`) and the frontend library (`frontend/src/lib`). The CI gates enforce
this (`pdm run test-ci` `--cov-fail-under`, and the vitest coverage thresholds); do
not lower a gate to make a change pass — raise coverage instead.

**No coverage padding.** Coverage is a by-product of testing behavior, never the goal:

- Every test must assert observable behavior and provide genuine **regression
  detection** — it must fail if the code's behavior regresses.
- No tests that merely execute lines without meaningful assertions; no mock-only tests
  that assert a mock was called; no snapshotting internal state to pad numbers. (The
  `check test quality` pre-commit hook guards this — do not evade it.)
- Prefer behavioral tests against real collaborators (local HTTP doubles, ephemeral
  QLever/pgvector, real SQLite) over mocking the unit under test.
- Tests must be resilient to reasonable refactoring — assert contracts and outputs,
  not implementation details.
- A retained test must be a reliable regression indicator: a relevant wrong production
  behavior must make it fail for the intended reason. Tests that merely execute code,
  assert mock choreography, mirror implementation logic in a fake, or prove fixture
  self-consistency are dead test code and must be deleted or replaced, never padded with
  token assertions.
- Every declared test suite must collect and execute at least one contract. Exit code 5
  / “no tests collected” is a failure, never a green empty lane.

**Contract / double-fidelity / data-shape tests are mandatory for external dependencies.**
TDD does not catch false assumptions about an external tool or about the real data: the test
and the code come from the same mental model, so the double encodes the same false belief as
the implementation, they agree, and the suite is green while the system is broken. On #73 this
produced ~12 bugs, *none* of them logic errors in our code. Whenever code depends on an
external tool (ROBOT/ELK, a DB driver, a serializer) or on real store data, add:
(a) a **contract test** asserting what the *tool itself* does (`test_reasoner_contract.py`);
(b) a **double-fidelity test** running the same input through the double and the real thing,
asserting the same verdict; (c) a **data-shape test** pinning what the *real store* looks like
(`test_upstream_data_contract.py`); and (d) a **gate-liveness** test proving each gate's reject
branch is reachable. The external tool must actually run in CI, or its tests silently skip and
the bugs stay invisible (ROBOT is now installed in the CI integration job for exactly this
reason). **Exception — data-shape contracts skip in CI by design**: they must interrogate the
*real* store, and seeding a fixture would make them assert facts about the fixture. They are a
**pre-merge local gate** (`pdm run test-integration-full-store` against the configured
live stores); a skip is not a pass. The safe default `pdm run test-integration` uses
nonce-owned disposable services and excludes `full_store`.

**Test types.** Use the registered markers deliberately: `unit`, `api`, `security`,
`integration` (real services), `mutating_integration` (nonce-owned disposable
resources), `full_store` (read-only configured real corpora), `full_build` (pinned build
/ real embeddings, excluded from the seeded-fixture CI run). Frontend: vitest unit +
component (jsdom) and Playwright e2e. `pdm run test` shows the per-type breakdown.

**A double-only green result is never acceptance for a real boundary.** Any change that
depends on PostgreSQL schema/data, QLever/SPARQL behavior, persisted JSON, an HTTP
upstream, a CLI/tool, adapter-node/browser behavior, Docker, or filesystem atomicity must
also run a production-path contract against the real disposable or configured boundary.
The real-boundary test must be part of the acceptance gate and must be observed RED for
the defect being fixed. A hand-authored FastAPI/QLever/store double may remain only when
the same input has a double-fidelity test against the production collaborator.

**If a change genuinely cannot reach 90% for a specific module** (e.g. a thin CLI glue
or an optional-dependency branch), that is a deliberate, justified exception — call it
out explicitly in the PR; never silently drop the gate.

### Documented coverage exceptions (current)

- **`backend`/`ontolib` `graph_store.py`** — the NCIt SPARQL-parsing layer is exercised
  by the disposable-QLever `integration` suite (combined into the strict `test-ci` gate);
  the gate still passes comfortably (~95%) on everything else.
- **Frontend `GraphExplorer.svelte` / `GraphMinimap.svelte`** — imperative sigma
  (WebGL) / 2d-canvas rendering shells that cannot mount in jsdom. Their pure logic is
  extracted to and unit-tested in `src/lib/graph/graph-explorer.ts` (reducers, color,
  layout seeding, node search, minimap projection); interactive behaviour is covered by
  the Playwright e2e graph flows. They are excluded from the vitest coverage `include`.

### Frontend testing gotcha

Fire-and-forget promise rejections inside a Svelte `$effect` (e.g. the `similar*` /
`mapped` fetch components) trip vitest's unhandled-rejection guard when a mock is reset
between tests. Use `mockClear` (not `mockReset`), and avoid asserting the rejected
branch through a rejected mock return — the happy/empty paths give the meaningful
coverage.
