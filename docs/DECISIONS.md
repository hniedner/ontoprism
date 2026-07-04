# Decisions

Running log of consequential decisions. Newest first. Each entry: context → decision → why.

## 2026-07-04 — library rename

### D10. Renamed the shared library `fairlib` → `ontolib`
Executed the rename that D1 deferred (to `ontolib`, not the placeholder `ontoprism-core`).
Changed: package dir `ontolib/src/fairlib` → `ontolib/src/ontolib`; every `from/import
fairlib` → `ontolib`; config paths (root pyproject editable/test/ruff/coverage/basedpyright,
the `ontolib`/backend pyprojects, pre-commit exclude, validation scripts); the root
`conftest.py` src roots; and the docs. `backend` and `frontend` keep their names.
*Why:* the fairdata-inherited name was misleading for an ontology-focused library.
Verified: `import fairlib` now fails, `import ontolib` resolves, full suite + lint green.
Older entries below predate this — the D6 import-collision reasoning now concerns the
`ontolib/` dir vs the `ontolib` package (same mechanism, new name).

## 2026-07-03 — M0 bootstrap

### D1. Porting method: lift whole packages, keep fairdata names
The original plan prescribed surgical, file-by-file extraction from `fairdata` with a
rename to `ontoprism-core`. **Superseded.** We instead **lift whole coherent packages**
from `fairdata`, keeping their names (`ontolib/`, `backend/`, `frontend/`) and imports
unchanged, so their real test suites come along and run unmodified. Rename to
`ontoprism-*` is deferred to a later, test-guarded mechanical pass. *(Later done: the
library was renamed `fairlib` → `ontolib` — see D10.)*
*Why:* avoids import-graph whack-a-mole; brings real behavioral tests for free; lowest
risk before a safety net exists. (User decision at kickoff.)

### D2. Lift scope: ontology vertical slice
Lift only the ontology platform slice: `ontolib` storage/terminologies/cadsr/core/common
(+ transitive deps); `backend` repository/graph/search/sparql/refresh routers + their
service/repo layers + middleware; `frontend` repositories/graph/results/query. Leave
behind the fairdata pipeline/HRM/learning/audit/CDE-mapping/target-spec subsystems
(~1M+ LOC, out of purpose). Addable later if needed. (User decision at kickoff.)

### D3. Testing: strict TDD, real behavioral tests, no padding/mocks
RED → GREEN → REFACTOR on every unit. Prefer `@pytest.mark.integration` tests against
the live services (Oxigraph :7878/:7879, Postgres :5432 — reachable from this dev shell)
over mock-heavy unit tests. No coverage-padding tests. When porting, port the real tests
first. (User directive at kickoff.)

### D4. Decomposition (M5) extracts from the STATED OWL, fetched first
Only `ThesaurusInferred.owl` is on disk and loaded in the running Oxigraph (inferred
build 26.05d). The assessment §4 requires the **stated** `Thesaurus.owl` to avoid
inferred-closure bleed (ancestor materialization + `Excludes_*` negatives). Decision:
**fetch the stated `Thesaurus.owl` from NCI EVS before M5** and extract from it; use the
inferred store only for validation/closure. The external download is confirmed with the
user when M5 begins. (User decision.) Only affects M5; M0–M4 unaffected.

### D5. Version pin: NCIt inferred build **26.02d** (corrected from assessment)
Integration/version-guard tests assert against `owl:versionInfo` **`26.02d`** — the value
the live store actually reports (verified 2026-07-03). The assessment §4 labeled it 26.05d,
but that is wrong; the triple count it quotes (12,836,426) matches, and C3262 → R105 → C12922
holds, so it's the same build under a mislabeled version. Roles are version-pinned; a build
bump must fail loudly.

### D6. pytest import mode = prepend + root conftest (not importlib)
Keep-names layout has top-level dirs (`ontolib/`, `backend/`) whose names equal the
packages. Under pytest's `importlib` mode, collecting a test at `ontolib/tests/…`
synthesizes the module `ontolib.tests.…`, which pre-binds `sys.modules["ontolib"]` to the
outer namespace dir and shadows the real `ontolib/src` package (top-level attrs like
`__version__` disappear). Decision: use `--import-mode=prepend` plus a root `conftest.py`
that prepends `ontolib/src` and `backend/src` to `sys.path` (runs in every xdist worker,
where editable `.pth` files are not processed).
*Trade-off:* prepend mode requires unique test-module basenames per directory. **Revisit
when lifting fairdata's large test suite** — fairdata uses importlib + a custom runner to
avoid basename collisions; port that strategy if collisions appear.

### D7. Editable local packages via default path backend
`ontolib` and `backend` are installed editable (PDM `[tool.pdm.dev-dependencies].local`,
`file://${PROJECT_ROOT}` syntax — PDM 2.28 crashes on the `-e ./pkg` relative form). Uses
the default path `.pth` backend (not the `editables` import-hook backend, which needs an
extra runtime dep and breaks under xdist). Import resolution in tests is guaranteed by the
root conftest (D6); the editable install serves runtime (uvicorn) and the type checker.

### D8. Local pre-commit is the primary quality gate (CI is parity, not discovery)
Lifted and trimmed fairdata's `.pre-commit-config.yaml` so lint/type/security/
test-quality failures are caught **locally before push**, not discovered by CI. Kept the
reusable gates (file hygiene, ruff + ruff-format, basedpyright full-project, gitleaks,
shellcheck, eslint, svelte-check, radon CC≥8) and lifted fairdata's genuinely-aligned
static scripts into `scripts/validation/`: `check_test_quality.py` (no mock-only /
coverage-padding tests — enforces D3), `check_broad_exceptions.py` (no silent-failure
swallowing), `check_complexity.py`. Dropped fairdata-ADR-specific hooks (phase-state
nuller, FDW001 http_error, exception-handler ordering, module/page-size, sync_versions)
and the heavy suites' hooks. CI runs the same `pre-commit run --all-files` for parity.
*Open policy:* `check_test_quality` reports mock-only tests as a **warning**, not a hard
block (some legitimate tests assert on interactions). Flip to hard-fail if we want
"no mockery" strictly enforced. Prettier deferred to the real frontend port (M4).

### D9. Full fairdata test_runner deferred to M1+
fairdata's `pdm run test` drives an ~8k-LOC `scripts/test_runner/` package (suite matrix,
JUnit parsing, dropped-test/silent-failure detection, colored summary) built for its large
suite set (phases, playwright, quality tiers) we do not have. Lifting it into a 2-test repo
is premature. Our `pdm run test*` scripts already mirror fairdata's naming with xdist
sharding + markers + coverage gate. Lift the runner alongside fairdata's actual test suites
in M1+, where its machinery is justified.

### Dropped/deferred tests
_(none yet — record here any intentionally-dropped ported test.)_
