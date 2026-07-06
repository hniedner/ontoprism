# Mini-Design — Regimen Decomposition Kind (`--branch regimen`)

**Status:** Design of record · **Date:** 2026-07-06 · **Issue:** [#4](https://github.com/hniedner/ontoprism/issues/4) · **Parent design:** [NCIt decomposition engine](./ncit-decomposition-engine.md) (§14 decision 2 defers regimen to this doc)

This is the design for the **second decomposition kind**. The main engine decomposes disease/neoplasm concepts along **semantic axes** (site + morphology + stage …), picking one most-specific filler per axis. A chemotherapy regimen is a fundamentally different object: it is a **mereological aggregate** — a *bag of drug components* — not an axis-qualified entity. This doc specifies how that kind plugs into the same engine without contorting the axis machinery.

---

## 1. Why regimen is a separate kind (the empirical case)

Queried against the running stated/inferred NCIt store (`localhost:7878`, build 26.05d):

| Fact | Value | Consequence for design |
|---|---|---|
| Regimen concepts (carry ≥1 `R123`) | **4,654** | the population; gated by presence of `R123`, not by a semantic-type list |
| Component edges (`R123`, stated) | **14,121** (~3.0 / regimen) | components are a **set**, not a single filler |
| Component edges under **inferred** closure | 52,423 | same ancestor bleed as neoplasm → **extract from the stated graph** |
| Nested components (a component that is itself a regimen) | **0** | flat bag — **no recursion**, no cycle handling needed |
| Other roles on regimens | only `R172` Regimen_Has_Accepted_Use_For_Disease (**1,550**) | one genuine **axis** rides alongside the component bag |

**The decisive difference:** the neoplasm engine's core operation is *most-specific filler selection* — collapse an axis's result set to its hierarchy leaf, dropping ancestors (`filler_selection.py`). For regimen components that operation is **wrong**: Doxorubicin, Bleomycin, Vinblastine and Dacarbazine in ABVD (`C9509`) are co-equal, orthogonal parts — none is an ancestor of another, none should be dropped. Regimen extraction must **keep every component**. Forcing this through the axis path would either mangle the semantics or require a special-case flag threaded through the whole selector; a distinct kind is cleaner.

---

## 2. Scope

**In scope:** the mereological decomposition of chemotherapy/drug regimens — every concept carrying an `R123` (`Chemotherapy_Regimen_Has_Component`) restriction (4,654 concepts), plus their single `R172` disease-indication axis where present.

**Out of scope:** dosing, schedule/cycle structure, and route — NCIt does not model these as roles on the regimen class, so they are not recoverable here (and are not part of the pre-coordination-refactoring goal). No NLP fallback and no minting: components are fully role-modelled and every filler is an existing active concept (§4), so both are unnecessary.

**Prerequisite:** ships **after** PRs 5a/5b (the neoplasm engine) land, because it reuses `stated_queries.py`, `legacy_writer.py`, `provenance.py`, and the `run.py` orchestrator.

---

## 3. Module layout — one new module, two small extensions

Reuses the parent package `ontolib/decomposition/`. Additions:

```
ontolib/src/ontolib/decomposition/
├── regimen_components.py   # NEW: mereological extractor (R123 bag + R172 axis)
├── detector.py             # EXTEND: regimen candidate = carries an R123 restriction
├── legacy_writer.py        # EXTEND: emit op:hasComponent (mereology) alongside op:hasConstituent
└── run.py                  # EXTEND: --branch regimen dispatches to the regimen kind
```

`regimen_components.py` is pure/deterministic like the rest of the core. It calls `stated_queries.py` with the `R123`/`R172` property IRIs and returns a typed `RegimenDecomposition`.

---

## 4. Extraction — mereological, not axis-based (`regimen_components.py`)

For each regimen candidate, against the **stated** graph:

1. **Components (`R123`)** → collect **all** `someValuesFrom` fillers into an unordered set of `Component(filler_code)`. **Most-specific collapse is explicitly disabled** for this edge — components are siblings, not an ancestor chain. Deduplicate identical IRIs only (defensive; the stated graph asserts each once).
2. **Disease indication (`R172`)** → this *is* axis-shaped (a single intended disease), so it goes through the **normal** axis path: most-specific selection over the stated result set, represented as an ordinary `Constituent(axis=R172, …)`. A regimen may have 0, 1, or several accepted uses; keep each most-specific indication.
3. **Constituent existence** — every `R123` filler is a Pharmacologic Substance / drug concept that already exists as an active `owl:Class` (verified: 20,021 role-target concepts are 100% existing, none deprecated — parent assessment §3.2). So `component_existence_rate` target is **100%** and **no minting path is invoked**.

Output: `RegimenDecomposition(code, components: list[Component], indications: list[Constituent])`.

The pipeline is strictly simpler than the neoplasm kind: no NLP fallback (§7 of the parent design), no morphology-from-parent, no `Excludes_*` filtering (regimens carry none).

---

## 5. Representation — additive, with a distinct mereology predicate

Extends the `op:` vocabulary (`https://w3id.org/ontoprism/vocab#`, per parent §14.3) with **one** new predicate so consumers can tell "is composed of these drugs" (part-of) apart from "fills these semantic axes" (qualification):

| Term | Meaning |
|---|---|
| `op:hasComponent` | regimen → a component node whose `op:filler` is a drug concept (mereological part-of) |
| `op:decompositionKind` | literal `"regimen"` on the source concept — the kind discriminator |

Everything else reuses the parent vocabulary unchanged: `op:representationStatus "legacy-precoordinated"` (a regimen *is* a pre-coordinated aggregate), `op:decomposedOn`/`op:decomposedBy`, and `op:hasConstituent` for the `R172` disease-indication **axis**.

Example (`C9509`, ABVD Regimen):

```turtle
ncit:C9509 op:representationStatus "legacy-precoordinated" ;
           op:decompositionKind "regimen" ;
           op:decomposedOn "2026-07-06"^^xsd:date ;
           op:hasComponent  [ op:filler ncit:C1326 ; op:axisSource "role" ] ;  # Doxorubicin Hydrochloride
           op:hasComponent  [ op:filler ncit:C313  ; op:axisSource "role" ] ;  # Bleomycin
           op:hasComponent  [ op:filler ncit:C931  ; op:axisSource "role" ] ;  # Vinblastine Sulfate
           op:hasComponent  [ op:filler ncit:C411  ; op:axisSource "role" ] ;  # Dacarbazine
           op:hasConstituent [ op:axis ncit:R172 ; op:filler ncit:C7702 ; op:axisSource "role" ] .  # Adult Hodgkin Lymphoma (indication)
```

Written **only** to `DECOMPOSED_GRAPH_IRI` (parent §4.1); the additivity guarantee and `test_additive_no_deletions` cover it unchanged.

### Postgres provenance — no migration change

Regimen rows reuse `decomp_constituent` (migration `0003_decomposition`) with **no schema change**:
- a component → `axis = 'op:Component'` (sentinel), `filler_code = <drug>`, `axis_source = 'role'`, `most_specific = false` (not applicable to a bag member).
- the `R172` indication → `axis = 'R172'`, `most_specific = true`, exactly like any axis constituent.

The sentinel `axis` value is sufficient for the writer to reconstruct which triples are `op:hasComponent` vs `op:hasConstituent`. `decomp_run.branch = 'regimen'` records the kind.

---

## 6. Metrics (`CoverageReport`, regimen fields)

| Metric | Definition | Expected |
|---|---|---|
| `pct_regimens_decomposed` | regimens with ≥1 extracted component / total regimen candidates | ~100% |
| `avg_components_per_regimen` | component edges / regimens | ~3.0 (14,121 / 4,654) |
| `component_existence_rate` | components resolving to an existing active concept / all | **100%** |
| `pct_with_disease_indication` | regimens carrying ≥1 `R172` indication | (report from run) |

`roundtrip_fidelity` / `owl:equivalentClass` is **not** in scope here: an equivalence assertion for a regimen would be an intersection over `op:hasComponent` — i.e. a *derivable post-coordinated regimen expression* — which is exactly the post-coordination-syntax concern owned by **#6**, deferred behind the same off-by-default seam as the neoplasm equivalence (parent §4.4).

---

## 7. Test-driven build plan

Strict TDD (repo standard). RED tests with fixtures captured from the stated store:

- `test_detect_regimen` — `C9509` (carries `R123`) flagged as a regimen candidate; a neoplasm concept (`C6135`) is **not** routed to the regimen kind; a plain drug (`C1326`, Doxorubicin) is not a regimen.
- `test_extract_regimen_components` — `C9509` → component set `{C1326, C313, C931, C411}` **exactly** (all four kept, none collapsed), asserted order-independently.
- `test_components_not_most_specific_collapsed` — a synthetic regimen whose components include an ancestor/descendant pair keeps **both** (proves the collapse is disabled for `R123`, the core deviation from the axis kind).
- `test_regimen_disease_indication_axis` — `C9509` → `R172` indication `C7702` (Adult Hodgkin Lymphoma) emitted as an `op:hasConstituent` axis, most-specific-selected.
- `test_component_existence` — every component filler resolves to an existing active `owl:Class` (100%); no minting record is produced for a regimen.
- `test_regimen_representation` — decomposing `C9509` emits `op:decompositionKind "regimen"` + `op:hasComponent` triples in the decomposed graph only; the original `C9509` and its `R123` axioms stay intact and resolvable.
- **Golden-file test** — a curated ~50-regimen sample → expected component/indication JSON; CI diff-gates it (mirrors the neoplasm golden spike).

Integration tests `@pytest.mark.integration`, version-pinned to the build, run against the live stated graph.

---

## 8. Phasing

**PR 5c — regimen kind** (after 5a/5b): `regimen_components.py`, the detector/writer/orchestrator extensions, the `op:hasComponent` predicate, the golden sample, and a `decomp_run` for `--branch regimen`. Small surface (one new module + three extensions), no new migration. `/pr-review-toolkit:review-pr` to zero findings before merge (pre-PR protocol).

---

## 9. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Component collapse via the axis selector drops co-equal drugs | Distinct kind; most-specific explicitly disabled for `R123`; `test_components_not_most_specific_collapsed` pins it |
| Inferred-closure inflation of components (52,423 vs 14,121) | Extract from the **stated** graph only, same as the neoplasm kind |
| Ambiguity between component (part) and indication (axis) in the graph | Distinct predicates (`op:hasComponent` vs `op:hasConstituent`) + `op:decompositionKind` discriminator |
| Scope creep into dosing/schedule | Out of scope by construction — NCIt models neither as a role; nothing to extract |
