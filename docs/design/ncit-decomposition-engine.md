# Design — NCIt Decomposition Engine (Issue #4 / Milestone M5)

**Status:** Design of record · **Date:** 2026-07-06 · **Issue:** [#4](https://github.com/hniedner/ontoprism/issues/4) · **Tracker:** #18 · **Serves:** #9 (M6 API/UI), #5 (balancing), #6 (post-coordination)

This is the design for the **engine** only — the core feature that gives OntoPrism its
name (turning a pre-coordinated NCIt into semantic modules). The serve/visualize layer
(#9) consumes the artifacts defined here but is out of scope. Empirical basis and the
decomposition model come from the companion
[NCIt decomposition assessment](./ncit-decomposition-assessment.md); this document turns
that assessment into an implementable, test-driven build.

---

## 1. Goal & definition of done

Produce a **non-pre-coordinated ("decomposed") view of NCIt**: for every pre-coordinated concept in scope, emit its constituent atomic concepts grouped by semantic axis, written **additively and reversibly** to a dedicated `ncit_decomposed` named graph, with a provenance record in Postgres and coverage metrics.

Mapped to the issue's checklist:

| Issue requirement | Delivered by |
|---|---|
| Decompose pre-coordinated concept → atomic constituents (roles-first, NLP fallback) | §5 detector → §6 filler selection → §7 NLP fallback |
| Additive & reversible: retain original, flag `legacy-precoordinated`, write to separate named graph, never mutate source | §4 data model, §8 legacy writer, §9 additivity guarantee |
| Preserve caDSR CDE→concept reachability | §4.3 — legacy code stays resolvable, constituents are existing IRIs |
| Surface decomposition in explorer (legacy + parts + reconstruction) | Read API/UI is **#9**; engine emits the graph #9 renders (§4) |
| Quality/coverage metrics (% decomposed, residual, round-trip fidelity) | §10 metrics + run manifest |

**Done when:** the engine produces `ncit_decomposed.ttl` + a `decomp_run` manifest for the neoplasm branch; unit + golden tests green; constituent-existence ≈100% on the roles path; the minted-concept list is bounded and explicit; an OWL-diff test proves the source graph is byte-for-byte unchanged.

---

## 2. Scope

**In scope (by NCIt semantic type):** Neoplastic Process (16,467 role-bearing), Disease or Syndrome (5,808), Cell or Molecular Dysfunction (3,911), and — secondary — Therapeutic/Preventive Procedure regimens (Chemotherapy_Regimen_Has_Component). These are the families where pre-coordination drives combinatorial concept explosion.

**Out of scope:** the molecular-biology role families — Gene (14,662), Amino Acid/Peptide/Protein (9,942), Enzyme, Receptor. Their roles (`Gene_Plays_Role_In_Process`, etc.) express genuine biology, not label-level aggregation; decomposing them yields no benefit. Enforced by a semantic-type gate in the detector (§5), which is also the guard against scope creep.

**Input graph:** the **stated** NCIt OWL, already loaded into the named graph `http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus-stated.owl` (`STATED_GRAPH_IRI`, `ontolib/terminologies/ncit/owl_load.py`). The inferred default graph is used only for validation (§10), never for extraction — this avoids the ancestor-closure bleed and the `Excludes_*` negative axioms documented in assessment §4.

---

## 3. Module layout

New package `ontolib/decomposition/`, pure/deterministic, no FastAPI or DB coupling in the core (persistence lives behind an interface). Mirrors the existing `terminologies/ncit/` style.

```
ontolib/src/ontolib/decomposition/
├── __init__.py
├── axes.py             # axis catalogue: role-code → semantic axis, defining vs excluded
├── detector.py         # is-pre-coordinated scorer + semantic-type gate
├── stated_queries.py   # SPARQL against the stated named graph (no inferred closure)
├── filler_selection.py # most-specific filler per axis; Excludes_* filter; morphology-from-parent
├── nlp_fallback.py     # label/synonym parser: laterality, with/without, staging version
├── constituent_index.py# resolve constituents to existing concepts; flag the mint tail
├── minting.py          # deterministic synthetic-id proposals for missing qualifiers
├── legacy_writer.py    # additive RDF builder → ncit_decomposed named graph / TTL
├── models.py           # Constituent, Decomposition, DecompRun, MintedConcept, CoverageReport
├── provenance.py       # Postgres persistence for run manifest + constituents + minted
└── run.py              # orchestrator + CLI (`pdm run decompose --branch neoplasm`)
```

The engine reads through the stated graph via a thin query layer (`stated_queries.py`) rather than reusing `role_queries.py` directly, because those builders query the default (inferred) graph and don't take a `GRAPH` clause. `stated_queries.py` reuses the same restriction-traversal *pattern* (`rdfs:subClassOf [ owl:onProperty ?r ; owl:someValuesFrom ?t ]`) wrapped in `GRAPH <STATED_GRAPH_IRI> { … }`, and reuses `safe_iri` for injection safety.

---

## 4. Data model — the decomposed representation

### 4.1 Named graph output

All engine output goes to a single named graph, kept separate from both the inferred default graph and the stated input graph:

```
DECOMPOSED_GRAPH_IRI = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus-decomposed.owl"
```

Loaded via the existing `OxigraphHttpClient.load(..., graph_iri=DECOMPOSED_GRAPH_IRI, replace=True)`. Serialized to `data/ncit_decomposed.ttl` as the portable artifact (so #9 / CI can load it without re-running the engine).

### 4.2 Vocabulary (ontoprism namespace)

A small ontoprism vocabulary, `ONTOPRISM_NS = "https://ontoprism.org/vocab#"`, carries the decomposition predicates so nothing collides with NCIt's own terms:

| Term | Meaning |
|---|---|
| `op:representationStatus` | literal `"legacy-precoordinated"` on each decomposed source concept |
| `op:decomposedOn` | xsd:date of the run that produced the decomposition |
| `op:decomposedBy` | literal run id (joins to `decomp_run.id`) |
| `op:hasConstituent` | source concept → a constituent node (blank node) |
| `op:axis` | constituent node → the axis IRI (reuse the NCIt role IRI where one exists, else an `op:` axis like `op:Morphology`, `op:Laterality`) |
| `op:filler` | constituent node → the filler concept IRI (existing NCIt concept or minted `op:` concept) |
| `op:axisSource` | literal `"role"` \| `"nlp"` \| `"parent"` — provenance of *how* the axis was recovered |
| `op:mostSpecific` | boolean — filler is the hierarchy leaf chosen over ancestors (audit aid) |

Example (`C6135`, matching assessment §5):

```turtle
ncit:C6135 op:representationStatus "legacy-precoordinated" ;
           op:decomposedOn "2026-07-06"^^xsd:date ;
           op:hasConstituent [ op:axis ncit:R88  ; op:filler ncit:C27970 ; op:axisSource "role" ] ;  # Stage III
           op:hasConstituent [ op:axis op:StageSystem ; op:filler ncit:C90530 ; op:axisSource "role" ] ;  # AJCC v7
           op:hasConstituent [ op:axis ncit:R101 ; op:filler ncit:C12400 ; op:axisSource "role" ; op:mostSpecific true ] ;  # Thyroid Gland
           op:hasConstituent [ op:axis ncit:R105 ; op:filler ncit:C36761 ; op:axisSource "role" ; op:mostSpecific true ] ;  # Neoplastic Neuroendocrine Cell
           op:hasConstituent [ op:axis op:Morphology ; op:filler ncit:C… ; op:axisSource "parent" ] .  # Medullary Carcinoma
```

### 4.3 Reversibility & caDSR preservation

- **Additive:** the engine writes *only* to `DECOMPOSED_GRAPH_IRI`. The stated and inferred graphs are never targets of a write. A consumer that ignores the new graph sees today's NCIt unchanged.
- **caDSR reachability:** CDE→concept mappings key on the NCIt concept IRI (`ncit:Cxxxxx`), which is untouched — the legacy code keeps its label, definition, and all axioms and stays fully resolvable. Every constituent filler is itself an existing active NCIt IRI (100% coverage on the roles path), so a decomposed concept remains reachable from its CDEs and every constituent is a valid navigation target.

### 4.4 Optional post-coordination equivalence (deferred to #6)

For entities with a clean, complete axis set the engine *can* additionally assert an `owl:equivalentClass` intersection of the fillers, which is what makes a legacy concept a *derivable* post-coordinated expression and enables de-duplication of AJCC v7/v8 and with/without forks. This is emitted behind a `--emit-equivalence` flag, **off by default** — it is the seam #6 (post-coordination syntax) builds on and should not gate the M5 additive deliverable.

### 4.5 Postgres provenance (Alembic migration `0003_decomposition`)

The graph is the queryable artifact; Postgres holds the run state for resumability, metrics, and the minted-concept governance list.

```
decomp_run
  id            text PRIMARY KEY        -- e.g. "neoplasm-2026-07-06T…"
  branch        text NOT NULL           -- "neoplasm" | "disease" | "regimen"
  status        text NOT NULL           -- "running" | "complete" | "failed"
  ncit_version  text NOT NULL           -- pinned build (owl:versionInfo), e.g. "26.05d"
  started_at    timestamptz NOT NULL
  finished_at   timestamptz
  metrics       jsonb                   -- CoverageReport (§10)

decomp_constituent
  run_id        text NOT NULL REFERENCES decomp_run(id)
  concept_code  text NOT NULL           -- the decomposed (source) concept
  axis          text NOT NULL           -- role code or op: axis
  filler_code   text NOT NULL           -- constituent concept (may be minted)
  axis_source   text NOT NULL           -- "role" | "nlp" | "parent"
  most_specific boolean NOT NULL
  PRIMARY KEY (run_id, concept_code, axis, filler_code)

minted_concept
  id            text PRIMARY KEY        -- deterministic synthetic id (§7.2)
  run_id        text NOT NULL REFERENCES decomp_run(id)
  axis          text NOT NULL           -- e.g. op:Laterality
  label         text NOT NULL           -- "Left", "Without Pleural Effusion"
  source_signal text NOT NULL           -- the label span / rule that produced it
  status        text NOT NULL DEFAULT 'proposed'  -- proposed | approved | rejected
```

`minted_concept.status` is the governance hook: minting never silently creates a clinical entity — it records a *proposal* for curator review (assessment §6.3, §7).

---

## 5. Detector — is a concept pre-coordinated? (`detector.py`)

Deterministic scorer, config-driven thresholds. A concept is a decomposition candidate when **all** hold:

1. **Semantic-type gate** — its `P106` semantic type is in the in-scope set (§2). This is the hard scope boundary; gene/protein concepts fail here.
2. **Defining-role count ≥ 2** — it carries ≥2 defining role restrictions in the *stated* graph (assessment: 55,044 concepts corpus-wide). Exactly-1-role concepts (12,818) are borderline and gated by config `min_defining_roles` (default 2); single-axis concepts are largely already atomic-adjacent.
3. Not itself a pure qualifier/value-set node (excluded by semantic type).

Output: `DetectionResult(code, is_precoordinated: bool, defining_role_count: int, semantic_type: str, label_multi_aspect: bool)`. The `label_multi_aspect` flag (from the FLAT NLP scan markers: hyphens, "of the", "with", "stage/grade", parentheses) is advisory — it routes a concept to the NLP fallback even when roles already cover it, to catch label-only axes.

`Excludes_*` roles (`Disease_Excludes_Abnormal_Cell`, `Disease_Excludes_Finding` — 35,662 + 30,009 negative axioms) are **not** counted as defining roles; they're filtered in `axes.py`.

---

## 6. Filler selection — most-specific per axis (`filler_selection.py`)

The core engineering. For each defining axis of a candidate, choose the single intended filler.

- **Working from the stated graph eliminates most ancestor bleed** — the stated form asserts only the intended filler, not the closure. This is why §2 mandates the stated input.
- **Defense-in-depth most-specific selection:** where an axis still yields multiple fillers, keep the hierarchy **leaf** — the filler with no other returned filler as its `rdfs:subClassOf`-ancestor. Ancestors within the same axis result set are dropped. (Guards against any residual closure and against genuinely multi-filler axes.)
- **`Excludes_*` filter:** negative-axiom restrictions are removed before selection (§5).
- **Morphology-from-parent:** morphology is not a role; it is carried by the taxonomic parent (e.g. `C6135`'s parent *Medullary Carcinoma*). The `op:Morphology` axis filler is derived from the nearest named parent whose semantic type is a morphology/neoplasm-by-morphology type, tagged `op:axisSource "parent"`.
- **Anatomy validation:** multi-parent anatomy fillers are cross-checked against the Uberon store (`:7879`, `UBERON_NS`) and NCIt's own anatomy hierarchy; ambiguous cases are flagged (`review` marker in the constituent record) rather than silently resolved.

Output per concept: `list[Constituent(axis, filler_code, axis_source, most_specific, needs_review)]`.

### 6.1 Stated encoding is *layered defined classes* (verified 2026-07-06)

**Correction to the initial extraction assumption**, found once the stated build was
loaded (10.84M triples) and the roles-first path was run against real data. The stated
graph does **not** hang a concept's role restrictions off `rdfs:subClassOf` — that is the
*inferred* build's flattened form (what `role_queries.py` reads on the default graph).
In the **stated** build a pre-coordinated concept is a **defined class**, expressed as a
chain:

```
C6135  owl:equivalentClass [ owl:intersectionOf ( C141041  [R88 someValuesFrom C27970] ) ]
C141041 owl:equivalentClass [ owl:intersectionOf ( C3879   [ …stage-system… ] ) ]
C3879   owl:equivalentClass [ owl:intersectionOf ( …genus… [ …site / abnormal-cell… ] ) ]
…                                                     ↓ (eventually a *primitive* class)
```

Each level intersects a **genus** (a named class) with one or a few **restrictions**;
the axes are distributed **up the genus chain**, not all present on `C6135`. So the
merged 5a query (`build_role_restrictions_query`, direct `rdfs:subClassOf` only) returns
**nothing** for `C6135` — its integration test is marked `xfail` pending this fix.

**Implication for extraction (next #4 increment):** collect restrictions by **recursively
walking the genus chain** — from the concept, follow
`owl:equivalentClass/owl:intersectionOf/(rdf:rest*/rdf:first)` to its members; a member
that is a **restriction** yields a role; a member that is a **defined** named class (has
its own `owl:equivalentClass`) is recursed; a member that is a **primitive** named class
is the terminal genus / morphology-bearing parent (§6 morphology-from-parent) and is
**not** recursed further (that bounds the walk and avoids climbing the general taxonomy).
This must be **application-level recursion** (query one level, recurse in Python):
Oxigraph does not evaluate the nested `rest*` inside a transitive `(…)+` property path,
so a single-path traversal is not viable. Most-specific selection (§6) still applies per
axis after the chain is gathered.

---

## 7. NLP fallback + minting (`nlp_fallback.py`, `minting.py`)

For axes that live only in the label (assessment §3.4): laterality (Left/Right/Bilateral), staging-manual version where not carried as a stage-system filler, and "with/without `<finding>`" negation.

### 7.1 Extraction
Rule/pattern-based (not a model) for determinism and testability: a small typed grammar over the preferred label + synonyms emitting `AspectRecord(axis, surface_form, polarity)`. Laterality and "with/without" are the primary yields; the finding concept itself usually already exists as a role filler, so only the *negation*/qualifier is new.

### 7.2 Minting (proposals, never silent creation)
When an NLP aspect has no existing NCIt concept (e.g. an explicit *absent/excluded* qualifier, or a laterality value not modelled), emit a **deterministic** proposal:

- **Stable synthetic id:** `op:MINT-{sha1(axis + '|' + normalized_label)[:12]}` — same input always yields the same id, so reruns are idempotent and diffable.
- Written to `minted_concept` with `status='proposed'` + `source_signal`, and linked in the graph exactly like any other constituent (`op:filler op:MINT-…`).
- The mint tail is expected to be **low hundreds**, concentrated in qualifier/value-set nodes, not clinical entities (assessment §3.4).

`test_missing_constituent_minting` pins that an NLP-only aspect produces a proposal record — never a silent create.

---

## 8. Legacy writer (`legacy_writer.py`)

Pure function: `(source_code, list[Constituent], run) → RDF triples` in `DECOMPOSED_GRAPH_IRI`. Emits the §4.2 vocabulary. Buffers to a `data/ncit_decomposed.ttl` file and bulk-loads via `client.load(..., graph_iri=DECOMPOSED_GRAPH_IRI)`. No `DELETE`, no write to any other graph — enforced structurally (the writer only ever targets one graph IRI) and verified by the additivity test.

---

## 9. Run orchestration & CLI (`run.py`)

```
pdm run decompose --branch neoplasm --out data/ncit_decomposed.ttl [--load] [--emit-equivalence] [--resume RUN_ID]
```

Pipeline per branch: enumerate in-scope concepts (semantic-type filter) → detect → for each candidate: stated-query roles → filler-select → NLP fallback → resolve/mint constituents → buffer triples + provenance rows. Writes the `decomp_run` manifest incrementally (status `running`→`complete`), so `--resume` restarts from the last persisted concept. `--load` pushes the TTL into Oxigraph; default is file-only (CI-friendly, deterministic).

**Version pinning:** the run records `owl:versionInfo` of the stated graph and refuses to reuse a manifest across a version bump (roles are version-pinned — assessment §4). A guard test fails loudly if the loaded build differs from the pinned build.

---

## 10. Quality / coverage metrics (`CoverageReport`, stored in `decomp_run.metrics`)

| Metric | Definition |
|---|---|
| `pct_decomposed` | in-scope candidates decomposed / total in-scope |
| `constituent_existence_rate` | fillers resolving to an existing active concept / all fillers (target ≈100% on roles path) |
| `residual_precoordination` | candidates left with an unresolved multi-aspect label after roles+NLP |
| `minted_count` | size of the mint tail (governance signal — should stay low hundreds) |
| `roundtrip_fidelity` | when `--emit-equivalence`: fraction of concepts whose emitted `owl:equivalentClass` re-derives the same constituent set (validated against the **inferred** graph as the closure oracle) |
| `needs_review_count` | ambiguous anatomy / multi-filler axes flagged for curation |

The inferred default graph is the validation oracle here: constituent existence and round-trip closure are checked against it even though extraction never reads from it.

---

## 11. Test-driven build plan

Strict TDD (repo standard): failing test → minimum code → green → ruff + basedpyright clean → commit. RED tests, with fixtures captured from the running stated store:

- `test_detect_precoordination` — `C6135` (≥2 roles, in-scope type) flagged; `C12400` (Thyroid Gland, atomic) not; a gene concept fails the semantic-type gate.
- `test_extract_constituents_roles_first` — `C6135` → {stage C27970, stage-system C90530, primary-site C12400, abnormal-cell C36761, morphology-from-parent}; `Excludes_*` filtered; most-specific filler chosen over ancestors.
- `test_constituent_existence` — every roles-path constituent resolves to an existing active `owl:Class` (≈100%).
- `test_most_specific_filler` — given an axis result set {Thyroid Gland, Endocrine Gland, …Neck}, selection returns only Thyroid Gland.
- `test_nlp_fallback_laterality` — `C4791` (Left Atrial Myxoma) → laterality=Left recovered from label; emits a needs-qualifier record.
- `test_missing_constituent_minting` — NLP-only aspect with no concept → deterministic proposal (stable id + provenance), not a silent create; rerun yields the same id.
- `test_legacy_representation` — decomposing `C6135` leaves the original intact and adds `representationStatus="legacy-precoordinated"` + `hasConstituent` triples in the decomposed graph only; the original code still resolves.
- `test_additive_no_deletions` — OWL-diff of the stated + inferred graphs before/after a run is empty (structural additivity guarantee).
- `test_equivalentclass_roundtrip` (optional, `--emit-equivalence`) — for a fully-covered entity the emitted intersection re-derives the constituent set; AJCC v7/v8 forks (`C6135`/`C141045`) become equivalent up to the stage-system qualifier.
- **Golden-file test** — a curated ~200-concept neoplasm sample → expected constituent JSON; CI diff-gates the golden output (this is the assessment §7 de-risking spike, promoted into a regression gate).

Integration tests marked `@pytest.mark.integration` run against the live stated graph and are version-pinned.

---

## 12. Phasing & PR cadence

Split M5 into two PRs (matches the plan's 5a/5b split), each `/pr-review-toolkit:review-pr` to zero findings before merge (pre-PR protocol):

- **PR 5a — detect + extract:** `axes.py`, `detector.py`, `stated_queries.py`, `filler_selection.py`, `models.py`, the golden-file spike over ~200 neoplasm concepts. Deliverable: a pure decomposition function + coverage numbers, no writes.
- **PR 5b — write + persist + CLI:** `nlp_fallback.py`, `minting.py`, `constituent_index.py`, `legacy_writer.py`, `provenance.py`, migration `0003_decomposition`, `run.py` + CLI, additivity test, run manifest. Deliverable: `ncit_decomposed.ttl` + `decomp_run` for the neoplasm branch.

#9 (M6 API/UI) starts once 5b lands the named graph.

---

## 13. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Inferred-vs-stated confusion (ancestor bleed, `Excludes_*`) | Extract from the **stated** graph only; most-specific selection as defense-in-depth; inferred used solely as validation oracle |
| Most-specific errors on multi-parent anatomy | Uberon + NCIt-hierarchy cross-check; ambiguous cases flagged `needs_review`, not silently resolved |
| Semantic loss on "without/excludes" | Model absence explicitly as a minted qualifier + `polarity`; never drop the negation |
| Consumer breakage | Additive-only by construction (single output graph, no deletions), proven by `test_additive_no_deletions` |
| Scope creep into gene/protein | Semantic-type gate in the detector is the hard boundary |
| NCIt version bump silently changes roles | Version-pinned run manifest + guard test that fails on a build mismatch |

---

## 14. Resolved decisions

The four decisions flagged during design are resolved below (grounded in the assessment data, the code, and the issue tracker). Each records the call and the rationale.

1. **`min_defining_roles` — keep default 2, but gate on ≥2 *decomposable axes*, not raw roles.**
   First, a correction: **55,044 is the corpus-wide count** (all 204K classes, incl. the out-of-scope gene/protein families). It must **not** be used as the in-scope candidate figure — the semantic-type gate (§5.1) fires first, so the true candidate set is measured *after* the gate over the ~26K in-scope role-bearing concepts (Neoplastic Process 16,467 + Disease or Syndrome 5,808 + Cell/Molecular Dysfunction 3,911). The golden spike must report that gated number, not 55,044.
   Second, the gate itself: count **decomposable axes** = stated defining roles **+** morphology-from-parent (§6) **+** label-signalled axes (`label_multi_aspect`, §5). A concept with a single site role but a morphology-bearing taxonomic parent is genuinely 2-axis (site + morphology) and must qualify; a raw `role_count ≥ 2` test would wrongly drop it, while truly single-axis nodes (one role, atomic parent, no label signal) are still excluded. Config key stays `min_defining_roles` (default 2) for the role component; the axis-count framing is the detector's actual predicate.

2. **Regimen branch — deferred, and it needs its own mini-design (not just a later run).**
   `Chemotherapy_Regimen_Has_Component` (14,121 axioms) is **mereological** — a regimen *has drug components* — not the site/morphology/stage **axis** model this engine is built around. It does not fit the axis catalogue, most-specific-filler selection, or morphology-from-parent machinery, so folding it into 5a/5b would force two different decomposition semantics into `axes.py`/`filler_selection.py`. Keep it out of the first pass; when it lands it gets its own small design and a distinct `--branch regimen` decomposition kind. Neoplasm + disease first. **Mini-design:** [NCIt regimen decomposition](./ncit-regimen-decomposition.md) (PR 5c).

3. **Vocabulary namespace — `https://w3id.org/ontoprism/vocab#` (prefix `op:`).**
   Nothing in-repo pins `ontoprism.org`; the only canonical identifier is `github.com/hniedner/ontoprism`. A **w3id.org persistent identifier** is the right choice: it is community-standard for linked-data/OBO vocabularies, is made resolvable via a one-line redirect PR to the w3id registry, and does **not** depend on owning (or keeping) the `ontoprism.org` domain — matching the repo's existing use of a purl persistent identifier for `UBERON_NS` (`namespaces.py`). Set `ONTOPRISM_NS = "https://w3id.org/ontoprism/vocab#"`. *Only* switch to `https://ontoprism.org/vocab#` if that domain is actually owned and committed to long-term; a namespace IRI need not resolve to be valid, but a stable, controllable one avoids a future migration of every `op:` triple.

4. **`owl:equivalentClass` emission — keep the off-by-default `--emit-equivalence` seam; #6 owns it. Confirmed, no change.**
   Issue **#6 ("Post-coordination expression syntax for observations & findings")** is a real, separately-tracked workstream, which settles the split: the formal `owl:equivalentClass` post-coordinated assertion — with its reasoner/consistency implications and AJCC-fork de-duplication payoff — belongs there. M5 already emits the constituent set that #6 consumes; the flag stays in the codebase as the documented seam, and `roundtrip_fidelity` (§10) is only computed when it is on.
