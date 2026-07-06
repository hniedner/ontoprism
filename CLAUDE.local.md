# CLAUDE.local.md — ontoprism project standards

Project-specific rules. Merge with the global `~/.claude/CLAUDE.md` and the pre-PR
protocol in project memory. These are enforced, not aspirational.

## Testing (non-negotiable)

**Strict TDD for all new implementations.** Write the RED test first (a real,
behavioral test that fails for the right reason), then the minimum code to make it
GREEN, then refactor with the tests green. No production code without a failing test
that motivated it.

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
  Oxigraph/pgvector, real SQLite) over mocking the unit under test.
- Tests must be resilient to reasonable refactoring — assert contracts and outputs,
  not implementation details.

**Test types.** Use the registered markers deliberately: `unit`, `api`, `security`,
`integration` (real services), `full_build` (pinned build / real embeddings, excluded
from the seeded-fixture CI run). Frontend: vitest unit + component (jsdom) and
Playwright e2e. `pdm run test` shows the per-type breakdown.

**If a change genuinely cannot reach 90% for a specific module** (e.g. a thin CLI glue
or an optional-dependency branch), that is a deliberate, justified exception — call it
out explicitly in the PR; never silently drop the gate.

### Documented coverage exceptions (current)

- **`backend`/`ontolib` `graph_store.py`** — the NCIt SPARQL-parsing layer is exercised
  by the live-Oxigraph `integration` suite (excluded from the hermetic `test-ci` gate);
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
