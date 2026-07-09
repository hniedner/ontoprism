# Decisions

Running log of consequential decisions. Newest first. Each entry: context → decision → why.

## 2026-07-08 — round-trip-fidelity architecture + R101 open items resolved

### D19. Reversibility is guaranteed by a complete, lossless representation of record; the single-most-specific view is a *lossy curated projection* on top of it — scope-correction to D15
D15 established "prefer the single most-specific filler per axis." §6.5 of the engine
design then found the sharper truth: a defined concept's full `owl:equivalentClass`
unfolding is *always* an exact, lossless definition over existing primitives, and **the
only source of fidelity loss is this project's own simplifications** — the small
defining-axis allowlist (`R88`/`R101`/`R105`, dropping `R103`/`R104`/`R106`/`R108`/…) and
collapsing each axis to one filler. README goal 4 requires the decomposition to round-trip
back to the original pre-coordinated NCIt concept. A single-valued, allowlist-filtered
view **cannot** satisfy that goal, so it cannot be the artifact of record.

**A necessary correction to D15's scope.** D15's "nothing is lost — the coarser fact stays
retrievable via subsumption" reasoning is sound **only** when the tied candidates are in an
is-a/part-of relationship (nested), because then the dropped fact is genuinely derivable
from the kept one (`C36825 ⊑ C36761`). It does **not** hold for the residual `R101`/`R105`
ties, which D16/D17/§6.4–§6.6 showed are *role-sense conflation*: genuinely co-equal,
**non-nested** facts (literal site `Lung` vs. lineage classification `Endocrine Gland`;
organ `Colon` vs. region `Colorectal Region`). Collapsing those to one leaf silently
discards a true, non-derivable statement — a real fidelity loss, not a harmless
projection. D15's most-specific rule is hereby scoped to **nested** candidate sets only;
non-nested co-equal values must be **preserved**, not collapsed.

**Decision (direction committed, full build deferred):**
1. **Artifact of record = the complete unfolding.** The reversible representation is the
   full multi-parent-DAG unfolding of the `owl:equivalentClass` intersection chain — every
   defining restriction, across every branch, with genuinely multi-valued axes kept
   multi-valued. This is lossless *by construction* (it *is* the concept's stated
   definition) and is what `roundtrip_fidelity` (§10) is measured against.
2. **Adopt SNOMED CT relationship groups as the target axis model.** Where an axis
   legitimately carries several non-nested values, represent them as grouped
   attribute-value sets rather than forcing one (loses information) or flattening
   everything into an undifferentiated bag (stops being a decomposition). This is the
   principled answer §6.5 identified, and it is what lets the co-equal site/lineage and
   region/organ facts coexist without either being dropped.
3. **The single-most-specific, allowlist-filtered output stays the near-term deliverable —
   explicitly flagged as a lossy curated projection**, derived *from* the complete
   representation, not the source of truth. It is the human-readable view a curator reads;
   it is not expected to round-trip and must not be relied on for reversibility.
4. **`owl:equivalentClass` emission is the seam that materializes the record-of-truth
   layer.** The off-by-default `--emit-equivalence` flag (design §4.4, §14.4, owned by #6)
   is retained and re-cast: it is not merely a post-coordination nicety, it is how the
   lossless artifact is asserted and how `roundtrip_fidelity` is validated against the
   inferred closure oracle.

**Why not build the full lossless+groups layer now:** the near-term deliverable (neoplasm
5a/5b) needs a curator-readable projection to make progress against the golden set, and the
relationship-groups model is only validated on a handful of concepts (§6.6). Committing the
architecture now — and forbidding the lossy collapse of non-nested values — prevents the
single-valued path from hardening into an irreversible design, while letting the complete
layer be built incrementally behind `--emit-equivalence`. Full rationale:
`docs/design/ncit-decomposition-engine.md` §6.5/§6.6, §4.4, §10; narrative: `tmp/PLAN_44.md`.

### D20. R101 needs two independent, composable refinements — resolves D17's open "region-vs-organ" question
D17 adopted genus-concept-sense classification (site-specific vs. lineage/histology-generic)
for the `R101`/`R105` role-sense conflation, and explicitly left open that the
**region-vs-organ** ties (`Colon`/`Colorectal Region`, `Left Atrium`/`Endocardium`) "don't
fit this lineage-generic-ancestor mechanism at all… two independent refinements to `R101`,
not one, is the working hypothesis pending further investigation." That hypothesis is now
resolved, using the evidence already gathered in §6.6.

**Decision:** `R101` primary-site disambiguation is handled by **two additive, composable
refinements, applied in order**, both routing to the D19 relationship-groups model rather
than forcing a single leaf:

1. **Genus-sense classification (D17)** — a restriction anchored on a genus concept
   classified *lineage/histology-generic* (empirically confirmed reusable ancestors:
   `C3010` Endocrine Neoplasm, `C3809` Neuroendocrine Neoplasm, `C3773` Neuroendocrine
   Carcinoma) is routed to a distinct axis `op:AssociatedLineageClassification`, **not**
   `R101`. This removes the `Endocrine Gland`/`Endocrine System` ties from the primary-site
   axis at their source. Handles the `Lung`-vs-`Endocrine Gland` class of tie.
2. **Filler-semantic-type ranking (new)** — for the residual, *non-lineage* ties, use the
   filler's own NCIt semantic type, which §6.6 confirmed **does** separate exactly this
   class (`Colon` "Body Part, Organ, or Organ Component" vs. `Colorectal Region`
   "Anatomical Structure"; `Left Atrium` organ vs. `Endocardium` "Tissue"). Prefer the
   organ-level filler ("Body Part, Organ, or Organ Component") as the `R101` primary site,
   and route the co-present region/tissue to a distinct grouped axis
   (`op:AssociatedRegion`) — again preserving both facts, not dropping one.

This is deliberately the signal D17 **rejected as a general classifier** — and that
rejection stands: semantic type fails on the lineage case (both `Lung` and `Endocrine
Gland` are typed "…Organ…"), which is precisely why refinement (1) must run **first** and
carve off the lineage sense before (2) is applied. The two refinements are complementary,
not competing: (1) is genus-anchored and removes lineage artifacts; (2) is filler-anchored
and orders what remains. Both are additive (new `op:` axes / metadata, never rewriting
`R101` triples), consistent with D17's additive principle and D19's groups model. Under
D19, neither is a "pick one" any longer — each tie becomes distinct grouped facts, so the
curated projection can still surface a single primary site while the record-of-truth layer
keeps every asserted site relationship. Validate via the same golden-set precision/recall
methodology as D14/D15/D17. Full evidence: `docs/design/ncit-decomposition-engine.md`
§6.4/§6.6; narrative: `tmp/PLAN_44.md`.

## 2026-07-08 — automated semantic versioning

### D18. Automated releases on merge to main; stay in `0.y.z` until the API is deliberately frozen
The repo had 27 merged PRs, no tags, a hand-maintained `CHANGELOG.md` `[Unreleased]`
section that had drifted behind reality, and five version fields (root/`ontolib`/
`backend` `pyproject.toml`, `ontolib/__init__.py`, `frontend/package.json`) that
disagreed (`0.1.0` vs `0.0.1`). **Decision:** adopt `python-semantic-release`, driven by
Conventional Commits, triggered by a `workflow_run` on a **successful CI run of a push
to main** — i.e. a PR merge whose merged tree is green.

Deliberate departures from the sibling `fairdata` workflow this was modelled on:
- **`major_on_zero = false`.** SemVer §4 reserves `0.y.z` for initial development. A
  breaking change bumps `0.7.x → 0.8.0`; it can never auto-promote to `1.0.0`. fairdata
  sets `major_on_zero = true` and then has to dodge the consequence by publishing
  `1.0.0-beta.N` prereleases, each of which needs a `gh release edit
  --prerelease=false --latest` fixup to be visible — a prerelease marked
  not-a-prerelease. Plain `0.y.z` says the same thing without the contradiction.
  `1.0.0` will be cut by hand (`semantic-release version --major`) when README's goals
  are met and the HTTP API is frozen.
- **One commit stamps all five manifests** via `version_toml`/`version_variables`,
  rather than fairdata's second `sync_versions.py` commit — which then has to be
  filtered back out of the next changelog via `exclude_commit_patterns`.
- **Release detection uses the action's `released` output**, not fairdata's
  `git describe --tags` probe, which reports `released=true` whenever *any* tag exists,
  including when no release was made.
- **A guard step refuses to release a commit that is no longer main's tip**, so a fast
  follow-up merge cannot be released twice or rewound.
- **`upload_to_pypi` is not set**: it was removed in python-semantic-release v8 and is
  silently ignored today. fairdata's config still carries it, where it does nothing.

Because only 27 of the 56 pre-tag commits used conventional subjects, prior versions
were reconstructed **from the merged-PR history, not from a commit parse** — a parser
replay would have dropped half of it. `scripts/dev/reconstruct_versions.py` pins seven
milestone tags (`v0.1.0`…`v0.7.0`) at the merge commits where each capability became
complete; it is idempotent and refuses to move an existing tag. Without those tags the
first automated release would restart at `0.0.0` (or, with defaults, announce three
months of work as `1.0.0`).

Conventional PR titles are enforced by `.github/workflows/pr-title.yml`: the parser
ignores merge commits and unpacks squash commits, so under squash-merge the PR title
*is* the release signal — a non-conventional title would otherwise silently produce no
release.

## 2026-07-08 — role-sense conflation finding + genus-classification strategy

### D17. Residual axis ambiguity is NCIt role-sense conflation, not a missing-atom gap — classify anchoring genus concepts additively, not a global role-splitting rewrite
D16 left an open question: is the R101/R105 ambiguity evidence that NCIt's existing
simple concepts are insufficient to represent pre-coordinated concepts' full semantics?
**No** — every filler examined across §6.4/D16's four concepts was verified primitive
(not itself a defined class); there is no case where a needed atomic concept is missing.
The actual finding is narrower and more precise: (a) a defined class's full
`owl:equivalentClass` unfolding is *always* an exact, lossless definition over existing
primitives — any fidelity loss comes from this project's own simplification choices
(a small defining-axis allowlist, single-valued-per-axis selection), not from NCIt; and
(b) NCIt's role vocabulary reuses `R101`/`R105` for pragmatically distinct senses — the
literal site/cell-type, and a broader lineage/histology classification inherited from an
organ-agnostic tumor-family ancestor. **Confirmed empirically, not just hypothesized:**
the identical ancestor concept `C3010 "Endocrine Neoplasm"` anchors the same
`R101 → Endocrine Gland/System` restriction in both `C6135` (thyroid) and `C35756`
(lung)'s genus DAGs — a systematic, reusable pattern, not a one-off.

**Decision:** adopt a genus-concept-sense classification strategy — proposed initially as
splitting the role and regenerating the graph with split roles before node decomposition;
refined, after checking the mechanism, to classifying the **genus concepts that anchor
overloaded restrictions** (site-specific vs. lineage/histology-generic) and persisting
that **additively** (new metadata/lookup, never rewriting the existing `R101`/`R105`
triples), consumed during per-level role extraction to route a restriction to its raw
role or to a new `op:` axis. This is a small, incremental classification problem (a few
hundred/thousand genus concepts that actually anchor decomposition-relevant restrictions)
building directly on D14's existing per-level DAG walk, not a rewrite of NCIt's ~10M
stated triples. A filler-semantic-type classifier was tested and rejected as the
general mechanism — it fails exactly on the cases that matter (`Lung` and `Endocrine
Gland` share a semantic type despite one being a lineage artifact).

**Resolved by D20 (above):** the region-vs-organ ties (`Colon`/`Colorectal Region`,
`Left Atrium`/`Endocardium`) don't fit this lineage-generic-ancestor mechanism at all —
a second, distinct refinement is needed there, using the semantic-type signal this
decision rejected for the lineage case. D20 confirms the two-independent-refinements
hypothesis and commits the order (genus-sense first, filler-semantic-type second), both
routed to D19's relationship-groups model rather than a forced single value.

Full rationale, evidence, and the SNOMED CT relationship-groups prior art comparison:
`docs/design/ncit-decomposition-engine.md` §6.5/§6.6; narrative: `tmp/PLAN_44.md`.

## 2026-07-08 — R101 anatomy resolution validated (partial), Uberon plan revised

### D16. NCIt's own is-a + `R82` part-of hierarchy resolves R101 anatomy ties partially, not fully — do not default to building a Uberon cross-check
D15 fixed the `R105` axis; the same investigation raised a hypothesis for `R101`
(primary site) ties: that combining `rdfs:subClassOf+` (is-a) with NCIt's own `R82
Anatomic_Structure_Is_Physical_Part_Of` role (walked transitively — it is not
transitively materialized in the inferred graph, unlike defining-role restrictions)
might resolve anatomy-axis ambiguity without needing the external Uberon store design
§6 originally scoped. **Before writing that into the design as settled, it was checked
against 4 concepts, not 1** (`C6135`, `C4791`, `C35756`, `C89995` — Thyroid, cardiac,
lung, and colon primaries respectively).

**Result:** the technique is a real, zero-downside improvement (it correctly eliminated
every genuine is-a/part-of container candidate across all 4 concepts, never wrongly) but
only fully resolved the tie in 1 of 4 cases (`C6135`). The other 3 have a recurring
residual tie between candidates that are simply *not related* in NCIt's own graph —
region-vs-organ (`Colorectal Region` vs `Colon`) and site-vs-cross-cutting-classification
(`Lung` vs `Endocrine Gland`, the same "neuroendocrine tumor" pattern D15 already found
on `R105`, recurring on `R101`). Only one sub-case (`Lung`/`Bronchus`, where real
anatomical containment exists but NCIt's own `R82` graph doesn't capture it) looks like a
plausible genuine Uberon win — one out of four concepts, not a validated general fix.

**Decision:**
1. Implement the is-a ∪ part-of (`R82`, transitive) extension to
   `filler_selection.py`'s most-specific selection — it is validated, low-risk, and
   reduces noise materially even where it doesn't fully resolve an axis.
2. Do **not** build a Uberon cross-check as the default follow-on plan. It is not shown
   to be the general fix; the residual ties look structural (NCIt models regions and
   organs, or anatomic site and tumor-lineage classification, as siblings rather than a
   specificity ladder), not a completeness gap a richer anatomy ontology obviously
   closes.
3. Treat residual `R101` ties the way `filler_selection.py` already treats any tied
   leaf set — `needs_review`, not a forced single answer. Expect this to be common on
   primary-site axes, not an edge case to engineer away.

Full data, per-concept tables, and reasoning: `docs/design/ncit-decomposition-engine.md`
§6.4; research code (untracked): `tmp/anatomy_resolve.py`; narrative: `tmp/PLAN_44.md`.

## 2026-07-08 — multi-parent DAG traversal + most-specific filler policy

### D15. Filler selection prefers the most-specific candidate across *alternate* DAG branches — resolves §6.2's "wrong constituent" framing as backwards
§6.2 recorded that most-specific selection over `C6135`'s collected `R105` (abnormal-cell)
candidates picks `C36825`, one level more specific than the assessment's expected `C36761`,
and called this "the wrong (too-specific) constituent." Investigating why (issue #44,
after D14 below) found `C36825` and `C36761` are asserted on **different** multi-
inheritance branches of the same DAG (`C36825` via genus `C3773`, `C36761` via genus
`C3809`; `C36825 ⊑ C36761` verified true via `ASK`) — both are simultaneously true
statements about `C6135`. This is not an extraction bug; it is a genuine choice between
two true statements at different specificity, and something had to decide which one a
single-valued axis reports.

**Decision:** prefer the most-specific true statement, even when the candidates come from
different alternate branches — §6.2's framing was backwards; `C36825` is the *correct*
answer for that axis, not a bug to work around. Grounded in:
- **Peer-reviewed precedent:** Spackman KA, "Normal forms for description logic
  expressions of clinical concepts in SNOMED RT," *Proc AMIA Symp* 2001:627-31 (PMID
  [11825261](https://pubmed.ncbi.nlm.nih.gov/11825261/)) — establishes canonical/normal
  forms for exactly this problem class: a concept's logical definition admits multiple
  equivalent representations, and one must be chosen for authoring/distribution.
- **Production precedent, same problem class:** SNOMED International's
  [`snomed-owl-toolkit`](https://github.com/IHTSDO/snomed-owl-toolkit/blob/master/documentation/calculating-necessary-normal-form.md)
  (the code that generates SNOMED CT's actual distributed release files) computes its
  Necessary Normal Form by explicitly removing attributes "redundant because they are
  less specific... in one of the alternate hierarchies." SNOMED CT has the same
  multi-parent-DAG structure NCIt does and resolves this exact scenario the same way, at
  production scale, for decades.
- **Consistent with this project's own round-trip-fidelity goal** (design §10): the more
  specific filler is required to exactly reconstruct the original pre-coordinated concept
  via `owl:equivalentClass`; the coarser filler only reconstructs a broader ancestor.
- **Nothing is lost:** because `C36825 ⊑ C36761`, the coarser fact stays retrievable via
  ordinary subsumption querying — asserting only the specific fact does not hide the
  general one from a consumer.

This resolves `filler_selection.py`'s existing most-specific behavior as *intentional
policy*, not an unchosen mechanical default. `docs/design/ncit-decomposition-engine.md`
§6.2/§6.3/§14 updated to match; the golden set's `C6135` entry
(`ontolib/tests/decomposition/golden/neoplasm.json`) is due to change from `C36761` to
`C36825` once golden-set curation resumes (issue #44).

### D14. Stated pre-coordination hierarchy is a multi-parent DAG, not a linear genus chain — correction to D13/§6.1
While building a defining-axis-filtered extractor (issue #44), walking `C6135`'s genus
chain level by level found that most levels have **two or three** named-class genus
members simultaneously (multiple inheritance), not one — e.g. `C3879
owl:equivalentClass [owl:intersectionOf (C160980 C4815 <2 roles>)]`. D13's own worked
example diagram reads as a linear chain; a walker that follows only one genus per level
(the natural reading of that diagram) silently drops whole branches. Verified
empirically: `C6135`'s golden-set-expected `R105→C36761` filler is asserted seven "genus
hops" down a branch (`C6135→C141041→C3879→C160980→C188222→C3809`) that a single-parent
walk never visits — dropping it produces a misleadingly plausible recall=0.75 result from
a genuinely incomplete traversal.

**Decision:** the recursive genus-chain walk (D13) must visit **every** named-class
member at each intersection level (breadth-first over the DAG, memoized so re-converging
branches aren't re-walked twice), not "the" genus. `scripts/decomposition_spike.py`'s
existing stack-based walk already does this correctly (it pushes every genus row it
finds); the mental model implied by D13's linear diagram does not, and a naive
reimplementation following that diagram will reproduce the bug. Research code:
`tmp/walk_intersection.py` (untracked, `tmp/` is gitignored — see `tmp/PLAN_44.md` for
the full investigation).

## 2026-07-06 — stated NCIt load + decomposition extraction

### D12. Load the stated NCIt OWL via the offline bulk loader, not HTTP GSP
The stated build (`Thesaurus.OWL.zip`, 713 MB extracted RDF/XML, 10.84M triples) is
ontoprism-specific (decomposition #4); fairdata never loaded it, so there was nothing to
clone. Pushing it through the HTTP Graph Store Protocol (`client.load` PUT) **OOM-killed
the Oxigraph container** (exit 137) on Docker Desktop's memory-limited VM. **Decision:**
load it with Oxigraph's offline bulk loader into the RocksDB dir —
`oxigraph load --location /data --file Thesaurus.owl --format application/rdf+xml --graph
<STATED_GRAPH_IRI> --non-atomic` (server stopped) — the same class of operation that
produced fairdata's cloned store. Loaded 10.84M triples in ~20s, memory-safe. HTTP GSP
stays for small/incremental writes (the decomposed named graph). *Also fixed a real bug:
`client.load` passed a sync file handle to httpx's `AsyncClient`, which rejects it — now
streamed as an async byte iterator (chunked).* Documented in `docs/DATA_SETUP.md`.

### D13. Stated pre-coordination is layered defined classes → recursive genus-chain extraction
Running 5a's roles-first extraction against the freshly-loaded stated graph revealed that
the stated build encodes a pre-coordinated concept as a **defined class** — an
`owl:equivalentClass`/`owl:intersectionOf` chain (genus + restriction per level) — not the
flat `rdfs:subClassOf` restrictions the *inferred* build materializes. So the merged 5a
query returns nothing for a defined class (e.g. `C6135`). **Decision:** extraction must
**recursively walk the genus chain** (application-level: query a level, recurse into
*defined* genus members, stop at *primitive* genus/morphology classes), because Oxigraph
won't evaluate the nested `rest*` inside a transitive property path. The C6135 integration
test is `xfail` until this lands (next #4 increment). Full rationale:
`docs/design/ncit-decomposition-engine.md` §6.1. *Why it matters:* this is the true core of
correct stated extraction, and only surfaced once real stated data was loaded — validating
the decision to load it before building 5b on top.

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
*Resolved policy (2026-07-08):* keep `check_test_quality`'s mock-only finding a **warning**,
not a hard block. D3 already makes live-service integration tests (`@pytest.mark.integration`
against Oxigraph/Postgres) the primary correctness gate, so mockery is not the load-bearing
signal here; and legitimate tests do assert on interactions (e.g. that `client.load` streams
an async byte iterator, D12), which a hard-fail would flag as false positives. The static
`check_broad_exceptions`/`check_complexity` gates plus the integration bar are the real
enforcement. Revisit only if mock-only unit tests start displacing behavioral coverage in
practice. Prettier deferred to the real frontend port (M4).

### D9. Full fairdata test_runner deferred to M1+
fairdata's `pdm run test` drives an ~8k-LOC `scripts/test_runner/` package (suite matrix,
JUnit parsing, dropped-test/silent-failure detection, colored summary) built for its large
suite set (phases, playwright, quality tiers) we do not have. Lifting it into a 2-test repo
is premature. Our `pdm run test*` scripts already mirror fairdata's naming with xdist
sharding + markers + coverage gate. Lift the runner alongside fairdata's actual test suites
in M1+, where its machinery is justified.

### Dropped/deferred tests
_(none yet — record here any intentionally-dropped ported test.)_
