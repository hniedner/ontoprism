# Design — NCIt Decomposition Engine (Issue #4 / Milestone M5)

This NCIt-specific decomposition design is not an enhanced-product compatibility claim.

**Status:** Design of record · **Date:** 2026-07-06 · **Issue:** [#4](https://github.com/hniedner/ontoprism/issues/4) · **Tracker:** #18 · **Serves:** #9 (M6 API/UI), #5 (balancing), #6 (post-coordination)

This is the design for the **engine** only — the core feature that gives OntoPrism its
name (turning a pre-coordinated NCIt into semantic modules). The serve/visualize layer
(#9) consumes the artifacts defined here but is out of scope. Empirical basis and the
decomposition model come from the companion
[NCIt decomposition assessment](./ncit-decomposition-assessment.md); this document turns
that assessment into an implementable, test-driven build.

For a plain-language entry point, see the [shared terminology](../../README.md#terminology).
This engineering specification then uses `axis`, `filler`, `genus`, `semantic type`, source
occurrence, partonomy, curated projection, and relationship group in their precise senses.

---

## 1. Goal & definition of done

Produce a **non-pre-coordinated ("decomposed") view of NCIt**: for every pre-coordinated concept in scope, emit its constituent atomic concepts grouped by semantic axis, written **additively and non-destructively** to a dedicated `ncit_decomposed` named graph, with a provenance record in Postgres and coverage metrics.

Mapped to the issue's checklist:

| Issue requirement | Delivered by |
|---|---|
| Decompose pre-coordinated concept → atomic constituents (roles-first, NLP fallback) | §5 detector → §6 filler selection → §7 NLP fallback |
| Additive & non-destructive: retain original, flag `legacy-precoordinated`, write to separate named graph, never mutate source | §4 data model, §8 legacy writer, §9 additivity guarantee |
| Preserve caDSR CDE→concept reachability | §4.3 — legacy code stays resolvable, constituents are existing IRIs |
| Surface decomposition in explorer (legacy + parts + reconstruction) | Read API/UI is **#9**; engine emits the graph #9 renders (§4) |
| Quality/coverage metrics (% decomposed, residual pre-coordination, minted count; unavailable fidelity) | §10 metrics + run manifest |

**Done when:** the engine produces `ncit_decomposed.ttl` + a `decomp_run` manifest for the neoplasm branch; unit + golden tests green; constituent-existence ≈100% on the roles path; the minted-concept list is bounded and explicit; an OWL-diff test proves the source graph is byte-for-byte unchanged.

---

## 2. Scope

**Hierarchy populations:** `neoplasm` is rooted at Neoplasm (`C3262`);
`disease` is rooted at Disease or Disorder (`C2991`) and therefore includes the
neoplasm population. Both are strict descendant closures of the stated named-class DAG:
direct named `rdfs:subClassOf` edges plus named genus members in
`owl:equivalentClass/owl:intersectionOf`. A bounded definition-list reader detects and
fails on a named genus beyond its supported prefix instead of silently truncating the
hierarchy. The branch fingerprint includes both root and scope algorithm version.

**Algorithm applicability (by NCIt semantic type):** the shared axis-qualified
decomposer accepts Neoplastic Process, Disease or Syndrome, and Cell or Molecular
Dysfunction. These types do not define either population: they gate whether this
algorithm applies after a hierarchy member has been selected. This distinction is
empirically necessary because NCIt has hierarchy members outside those types and
same-typed concepts outside the disease hierarchy.

**Out of scope:** the molecular-biology role families — Gene (14,662), Amino Acid/Peptide/Protein (9,942), Enzyme, Receptor. Their roles (`Gene_Plays_Role_In_Process`, etc.) express genuine biology, not label-level aggregation; decomposing them yields no benefit. The hierarchy population excludes unrelated families; the detector's semantic-type applicability gate is defense in depth. Regimens remain unavailable until their distinct component-bag algorithm is implemented.

**Input graph:** the **stated** NCIt OWL, already loaded into the named graph `http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus-stated.owl` (`STATED_GRAPH_IRI`, `ontolib/terminologies/ncit/owl_load.py`). The inferred default graph is used only for validation (§10), never for extraction — this avoids the ancestor-closure bleed and the `Excludes_*` negative axioms documented in assessment §4.

---

## 3. Module layout

New package `ontolib/decomposition/`, pure/deterministic, no FastAPI or DB coupling in the core (persistence lives behind an interface). Mirrors the existing `terminologies/ncit/` style.

```
ontolib/src/ontolib/decomposition/
├── __init__.py
├── axes.py             # axis catalogue: role-code → semantic axis; projected/defining/excluded
├── detector.py         # is-pre-coordinated scorer + semantic-type gate
├── stated_queries.py   # SPARQL against the stated named graph (no inferred closure)
├── filler_selection.py # routed-axis specificity; co-equal preservation; morphology-from-parent
├── nlp_fallback.py     # label/synonym parser: laterality, with/without, staging version
├── constituent_index.py# resolve constituents to existing concepts; flag the mint tail
├── minting.py          # deterministic synthetic-id proposals for missing qualifiers
├── legacy_writer.py    # deterministic RDF builder → ncit_decomposed named graph / TTL
├── models.py           # Constituent, Decomposition, DecompRun, MintedConcept, CoverageReport
├── provenance.py       # Postgres persistence for run manifest + constituents + minted
└── run.py              # orchestrator + CLI (`pdm run decompose --branch neoplasm`)
```

The engine reads through the stated graph via a thin query layer (`stated_queries.py`) rather than reusing `role_queries.py` directly, because those builders query the default (inferred) graph and don't take a `GRAPH` clause. `stated_queries.py` reuses the same restriction-traversal *pattern* (`rdfs:subClassOf [ owl:onProperty ?r ; owl:someValuesFrom ?t ]`) wrapped in `GRAPH <STATED_GRAPH_IRI> { … }`, and reuses `safe_iri` for injection safety.

---

## 4. Data model — the decomposed representation

### 4.1 Named graph output

The active published representation occupies one named graph, kept separate from both
the inferred default graph and the stated input graph:

```
DECOMPOSED_GRAPH_IRI = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus-decomposed.owl"
```

Publication loads sealed bytes into a run-scoped staging graph and atomically replaces
`DECOMPOSED_GRAPH_IRI` with a marker-guarded SPARQL update. The same bytes are serialized
to `data/ncit_decomposed.ttl` as the portable artifact.

### 4.2 Vocabulary (ontoprism namespace)

A small ontoprism vocabulary, `ONTOPRISM_NS = "https://w3id.org/ontoprism/vocab#"` (§14 decision 3, matching `vocab.py`), carries the decomposition predicates so nothing collides with NCIt's own terms:

| Term | Meaning |
|---|---|
| `op:representationStatus` | literal `"legacy-precoordinated"` on each decomposed source concept |
| `op:decomposedOn` | xsd:date of the run that produced the decomposition |
| `op:decomposedBy` | literal run id (joins to `decomp_run.id`) |
| `op:hasConstituent` | source concept → a constituent node (blank node) |
| `op:axis` | constituent node → the normalized `op:` axis IRI; only unknown roles retain their raw NCIt role code |
| `op:group` | constituent node → a relationship-group id (D19), persisted and read back unchanged |
| `op:filler` | constituent node → the filler concept IRI (existing NCIt concept or minted `op:` concept) |
| `op:axisSource` | literal `"role"` \| `"nlp"` \| `"parent"` — provenance of *how* the axis was recovered |
| `op:mostSpecific` | boolean — filler was chosen over an is-a ancestor (audit aid; R82-only collapse is not encoded by this flag) |
| `op:needsReview` | boolean — unresolved ordinary-axis ambiguity requiring curation |
| `op:sourceDefinitionFact` | role-/parent-derived constituent node → one or more deterministic complete-definition fact IRIs; minted NLP fallback uses proposal provenance instead |
| `op:hasDefinitionFact` | source concept → a typed complete-definition fact |
| `op:factKind`, `op:anchor`, `op:definitionGroup`, `op:definitionDepth` | fact type, anchoring genus, source-expression group, and DAG depth |
| `op:genus`, `op:isDefined`, `op:role`, `op:filler` | typed genus/restriction fact payload |
| `op:completeDefinitionIdentity`, `op:completeFactCount`, `op:projectedFactCount`, `op:projectionLossCount` | stable complete-record identity and projection-loss evidence |

Example (`C6135`, matching assessment §5). **Superseded in detail by D15/D19/D20** —
`ontolib/tests/decomposition/golden/neoplasm.json` is currently an `AUTO-DRAFT` for #57,
not an authoritative SME oracle. Its questions include the D15 cell-type choice and
D20's split of raw R101 candidates across primary-site, lineage, and region senses, with
co-equal non-nested fillers carrying groups per D19. The shape below illustrates the
predicate vocabulary but does not assert the pending adjudication:

```turtle
ncit:C6135 op:representationStatus "legacy-precoordinated" ;
           op:decomposedOn "2026-07-06"^^xsd:date ;
           op:hasConstituent [ op:axis op:StageValue ; op:sourceRole ncit:R88 ; op:filler ncit:C27970 ; op:axisSource "role" ] ;  # Stage III
           op:hasConstituent [ op:axis op:StageSystem ; op:sourceRole ncit:R88 ; op:filler ncit:C90530 ; op:axisSource "role" ] ;  # AJCC v7
           op:hasConstituent [ op:axis op:PrimarySite ; op:sourceRole ncit:R101 ; op:filler ncit:C12400 ; op:axisSource "role" ; op:mostSpecific true ] ;  # Thyroid Gland
           op:hasConstituent [ op:axis op:CellType ; op:sourceRole ncit:R105 ; op:filler ncit:C36761 ; op:axisSource "role" ; op:mostSpecific true ] ;  # Neoplastic Neuroendocrine Cell
           op:hasConstituent [ op:axis op:Morphology ; op:filler ncit:C… ; op:axisSource "parent" ] .  # Medullary Carcinoma
```

### 4.3 Reversibility & caDSR preservation

- **Source-preserving:** the engine never writes to the stated or inferred graphs. It
  atomically replaces only `DECOMPOSED_GRAPH_IRI`, so a consumer that ignores the output
  graph sees NCIt unchanged while consumers of that graph see one complete publication.
- **caDSR reachability:** CDE→concept mappings key on the NCIt concept IRI (`ncit:Cxxxxx`), which is untouched — the legacy code keeps its label, definition, and all axioms and stays fully resolvable. Every constituent filler is itself an existing active NCIt IRI (100% coverage on the roles path), so a decomposed concept remains reachable from its CDEs and every constituent is a valid navigation target.

### 4.4 Complete representation and proof-bearing equivalence quarantine

The current allowlist-filtered projection (§6) cannot prove that it preserves
the source's complete multi-parent and grouped definition. It is an explicitly lossy
curated projection and never asserts `owl:equivalentClass`. The reserved
`--emit-equivalence` option fails closed before settings, clients, provenance, stdout, or
filesystem effects; the writer independently refuses the same request.

The complete representation is now materialized independently of the projection (D50):
a bounded stated-only DAG of typed genus/restriction facts, with deterministic identities,
source-expression groups, anchoring genera, and role-/parent-projection trace links. Only this record
may feed future equivalence emission or measured `roundtrip_fidelity`. Its existence alone
does not prove those semantics, so D43's quarantine remains. The quarantine does not block
the additive constituent view or future post-coordination grammar; it prevents either the
curated view or an unvalidated structural capture from being misrepresented as an exact
definition.

### 4.5 Postgres provenance (Alembic migrations `0003`, `0008`–`0014`)

The graph is the queryable artifact; Postgres holds an exact, source-bound run manifest,
the materialized worklist, transactional results, metrics, and the minted-concept
governance list.

```
decomp_run
  id            text PRIMARY KEY        -- branch + UUID; collision-safe
  branch        text NOT NULL           -- "neoplasm" | "disease" (regimen reserved)
  status        text NOT NULL CHECK (...) -- "running" | "complete" | "failed"
  ncit_version  text NOT NULL
  source_identity text NOT NULL          -- D47 candidate identity
  fingerprint   jsonb NOT NULL           -- exact source/scope/worklist/config/modes/time
  fingerprint_sha256 text NOT NULL
  emitted_at    timestamptz NOT NULL     -- stable across resume
  started_at    timestamptz NOT NULL
  finished_at   timestamptz
  metrics       jsonb                   -- CoverageReport (§10)
  error_type/error_message text          -- bounded failure evidence

decomp_work_item
  run_id/concept_code PRIMARY KEY
  ordinal       integer NOT NULL         -- exact original order, including zero-output
  state         text NOT NULL CHECK (...) -- pending | running | failed | complete
  attempt_count integer NOT NULL
  claim_token   uuid                     -- fences stale workers
  outcome       text                     -- closed D56 classification; historical unknown
  semantic_type text                     -- retained projection of source P106
  semantic_types jsonb                   -- complete canonical source P106 value set
  flags/counts/error/timestamps           -- state/outcome-shape constrained

decomp_constituent
  run_id        text NOT NULL REFERENCES decomp_run(id)
  concept_code  text NOT NULL           -- the decomposed (source) concept
  axis          text NOT NULL           -- normalized op: axis (or unknown role)
  source_role   text                    -- NCIt role that produced the projection
  filler_code   text NOT NULL           -- constituent concept (may be minted)
  axis_source   text NOT NULL           -- "role" | "nlp" | "parent"
  most_specific boolean NOT NULL
  needs_review  boolean NOT NULL
  relationship_group text
  source_definition_ids jsonb NOT NULL
  PRIMARY KEY (run_id, concept_code, axis, filler_code)

decomp_definition_fact
  run_id/concept_code/fact_id PRIMARY KEY
  anchor_code/group_id/depth
  fact_kind     text NOT NULL            -- genus | restriction
  genus_code/is_defined                  -- genus shape
  role_code/filler_code                  -- restriction shape

decomp_minted_proposal
  run_id/concept_code/proposal_id PRIMARY KEY
  axis/label/source_signal/status

minted_concept
  id            text PRIMARY KEY        -- deterministic synthetic id (§7.2)
  run_id        text NOT NULL REFERENCES decomp_run(id)
  axis          text NOT NULL           -- e.g. op:Laterality
  label         text NOT NULL           -- "Left", "Without Pleural Effusion"
  source_signal text NOT NULL           -- the label span / rule that produced it
  status        text NOT NULL DEFAULT 'proposed'  -- proposed | approved | rejected
```

Creating a run and its ordered worklist is one transaction. A claim-token-fenced
per-concept transaction replaces its constituents/proposals and marks it complete;
failures roll back before bounded failure evidence is recorded. Resume accepts only a
matching running/failed fingerprint and processes exactly non-complete items. If the
source changes before publication, all partial result rows are invalidated before a retry.
Only successful run completion promotes run-scoped proposals into `minted_concept`, whose
status is the governance hook: minting never silently creates a clinical entity.

---

## 5. Detector — is a concept pre-coordinated? (`detector.py`)

Deterministic scorer, config-driven thresholds. A concept is a decomposition candidate when **all** hold:

1. **Semantic-type applicability gate** — its `P106` semantic type is supported by the
   branch's algorithm (§2). Hierarchy membership is the population boundary; this gate
   rejects members the axis-qualified algorithm does not claim to decompose.
2. **Decomposable-axis count ≥ 2** — distinct defining stated roles, a
   morphology-bearing parent, and a multi-aspect label each contribute axes. The default
   threshold is 2; a one-role concept can therefore qualify when it also has morphology
   or a label-signalled axis, while a truly single-axis concept does not.
3. Not itself a pure qualifier/value-set node (excluded by semantic type).

Output: `DetectionResult(code, is_precoordinated: bool, defining_role_count: int, semantic_type: str, label_multi_aspect: bool)`. The `label_multi_aspect` flag (from the FLAT NLP scan markers: hyphens, "of the", "with", "stage/grade", parentheses) is advisory — it routes a concept to the NLP fallback even when roles already cover it, to catch label-only axes.

`Excludes_*` roles (`Disease_Excludes_Abnormal_Cell`, `Disease_Excludes_Finding` — 35,662 + 30,009 negative axioms) are neither defining nor projected. Positive `R103` is
projected but deliberately does not contribute a defining detector axis; optional
`May_Have_*` roles are retained only in the complete structural record.

---

## 6. Filler selection — routed-axis specificity (`filler_selection.py`)

The core engineering. For each projectable positive source restriction of a candidate,
route it to an axis and choose the intended
filler or preserve unresolved co-equal fillers without silently discarding them.

- **Working from the stated graph eliminates most ancestor bleed** — the stated form asserts only the intended filler, not the closure. This is why §2 mandates the stated input.
- **Defense-in-depth most-specific selection:** use NCIt's stated `rdfs:subClassOf`
  hierarchy on every non-lineage axis and bounded transitive `R82` containment only on
  location axes. A filler is dropped only when it is strictly broader than another
  returned filler; unrelated or mutually broader fillers remain.
- **Projection filter:** `Excludes_*` negative axioms and optional R89/R111–R116
  `May_Have_*` roles are removed before selection (§5). Projectable non-defining `R103`
  is retained in the curated projection but does not help a concept pass the detector.
- **Morphology-from-parent:** morphology is not a role; it is carried by the taxonomic parent (e.g. `C6135`'s parent *Medullary Carcinoma*). The `op:Morphology` axis filler is derived from the nearest named parent whose semantic type is a morphology/neoplasm-by-morphology type, tagged `op:axisSource "parent"`.
- **Anatomy validation:** location specificity uses NCIt's own is-a plus bounded
  transitive `R82` part-of hierarchy. Unresolved ordinary axes receive `needs_review`;
  ambiguous routed region and stage-system values are retained as grouped,
  review-exempt facts, while lineage classifiers remain ungrouped.
  The selector does not consult Uberon; §6.4 found that external cross-check unsuitable
  as a general tie-break.
- **`R101` sense split (D20/§6.6):** before collapse, primary-site restrictions are disambiguated by two composable refinements — genus-sense classification (lineage-generic → `op:AssociatedLineageClassification`) then filler-semantic-type ranking (organ-level → `op:PrimarySite`; region/tissue → `op:AssociatedRegion`). Co-equal non-nested values are retained; selected routed region/stage axes may receive synthetic groups, while lineage classifiers remain ungrouped.
- **R101 change evidence (D77):** the v3→v4 boundary is a strict occurrence ledger keyed by the
  complete persisted structural occurrence identity. A removed broader same-axis projection is
  covered only by a retained new R101 link and a replayable directed stated-R82 path. One-step and
  closure-only evidence remain distinct; report mechanics cannot authorize content or open the
  publication gate. The tracked report is mechanically complete but content-pending and blocked
  (`pdm run python -c 'from pathlib import Path; from ontolib.decomposition.r101_conservation import load_r101_conservation_report; r=load_r101_conservation_report(Path("ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz")); print(r.mechanical_status,r.content_authorization.status,r.publication_gate)'`,
  2026-08-19).
- **R101 human review (D78):** the 3,291 R82-covered occurrences are frozen in a separate packet as
  162 endpoint patterns and 2,800 disease propositions. The workbook contains no occurrence audit
  sheet or internal IDs. Review asks only whether the retained more-specific site supplies
  non-exclusive projection coverage of the broader site for listed disease/source occurrences;
  source assertions remain preserved, multiple valid narrower sites remain independent, and no
  equivalence, universal, complete, or exclusive claim is recorded. Pattern decisions expand over
  immutable membership, with explicit reasoned disease exceptions. Import creates a proposed
  occurrence-level registry, and preflight only replays decision expansion with
  `writes_performed=false`; it cannot authorize or publish the pending report
  (`pdm run pytest ontolib/tests/decomposition/test_r101_review.py -q && pdm run test-integration-full-store -k r101_review_labels_match_real_qlever_in_bounded_batches`,
  2026-08-20).

Output per concept: `list[Constituent(axis, filler_code, axis_source, source_role, most_specific, needs_review, group)]`.

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
former merged 5a query (`build_role_restrictions_query`, direct `rdfs:subClassOf` only)
returned **nothing** for `C6135`. The implemented application-level walker now follows
the layered stated definitions, and its C6135 integration contract is active rather than
`xfail`.

**Implemented extraction:** collect restrictions by **recursively
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

### 6.2 The genus-chain walk over-collects — extraction is curation-heavy, not mechanical (verified 2026-07-06)

Building the recursive walk of §6.1 and running it on `C6135` against the loaded stated
graph produced a **critical negative result**: the walk reaches all four expected fillers
(`C27970`, `C90530`, `C12400`, `C36761`) **but also over-collects heavily** — ~30 visited
classes yielding dozens of extra fillers (`R108 Has_Finding`, `R113/R115 May_Have_*`,
`R135/R138/R139/R142 Excludes_*`, `R176 Mapped_To_Gene`, and *many* alternative
`R105 Has_Abnormal_Cell` values). The reason: the stated **upper** genus classes are
themselves richly pre-coordinated defined classes, so walking a concept's genus chain to
its base re-creates the very ancestor-closure bleed the stated form was supposed to avoid
(assessment §4) — just reconstructed one level up.

**Most-specific selection alone does not rescue it.** For `C6135`'s six collected `R105`
abnormal-cell fillers, most-specific selection over the *unfiltered* over-collected set
picks `C36825`. At the time this was recorded as "the wrong (too-specific) constituent" —
**that framing was backwards and is corrected by DECISIONS D15**: `C36825` and the
assessment's `C36761` are both genuinely asserted (on different DAG branches — §6.3), and
`C36825` is in fact the *intended* most-specific answer once the axis set is correctly
filtered first. The real problem this example exposed was over-collection (the ~30 extra
classes and their non-defining roles), not most-specific selection itself.

**Conclusion.** Correct stated extraction is **not** a mechanical genus-walk + most-specific
over the *unfiltered* result set. It needs (a) explicit projectable-role classification
(including retained positive non-defining roles while dropping optional, negative, and
unmapped roles), (b) a principled boundary that
distinguishes a concept's *own differentia* from axes it merely *inherits* from its genus
(per-level differentia diffing against the genus — validated in §6.3), and (c) curation of
axes that remain genuinely ambiguous after (a)+(b) (§6.3's R101 example). This is exactly
the **filler-selection tooling + curation** effort the assessment scoped at multiple
person-months (§6–§7); §6.3 reports the first concrete, measured progress against it. The
The C6135 walker and extraction integration contracts now run normally. Authoritative
precision/recall acceptance still depends on the SME-validated golden set; §6.3's numbers
are evidence, not the finish line.

### 6.3 Resolution direction, validated against C6135 (issue #44, 2026-07-08)

Two corrections to §6.1/§6.2, recorded as DECISIONS D14/D15, turn the negative result above
into a measured positive one:

- **D14 — the genus chain is a multi-parent DAG, not a linear chain.** Most levels have
  *two or three* named-class genus members (multiple inheritance), not one. A walk that
  visits every genus member (breadth-first, memoized) is required — following only one
  branch silently drops real content (this is how `C36761` was found to be reachable at
  all: seven hops down a branch a linear walk never visits).
- **D15 — filler selection prefers the most-specific candidate across alternate branches.**
  When two branches assert different specificities of the same axis (`C36825` vs `C36761`,
  both true), keep the more specific one — matching SNOMED CT's Necessary Normal Form
  precedent (D15's full rationale + citations).

Applying (a) a small **defining-axis allowlist** (`R88` stage, `R101` primary site, `R105`
abnormal cell — extend as more concepts are curated) at **every** level of a full
multi-parent DAG walk, before most-specific selection, measured on `C6135` against the
existing 1-concept golden set:

| Extractor | Precision | Recall |
|---|---|---|
| Naive walk, no axis filter (§6.2's original result) | 0.10 | 0.75 |
| + defining-axis allowlist, full multi-parent DAG walk | 0.31 | **1.00** |

Recall reaching 1.00 confirms D14 was necessary (the golden set's `R105→C36761` is only
reachable through the previously-unwalked branch). The remaining precision loss is now
concentrated on exactly two axes, for two different and well-understood reasons — not
diffuse noise:

- **R101 (primary site):** 5 raw candidates spanning organ/system/region granularity
  (`C12400` Thyroid Gland, `C12704` Endocrine Gland, `C12705` Endocrine System, `C12418`
  Head and Neck, `C13063` Neck). Most-specific selection over the *stated* graph's
  `rdfs:subClassOf+` (is-a only) resolves only 1 ancestor pair among them, leaving 4 tied
  "leaves" — because `C12418`/`C13063` are anatomic *regions*, not organs, and aren't on a
  strict is-a chain with `C12400`/`C12704`/`C12705` at all. **Update, validated against 4
  concepts total: §6.4** — the fix is real but partial, not the clean resolution first
  hoped for.
- **R105 (abnormal cell):** resolved by D15 (policy: most-specific wins).

**A soundness caveat surfaced during this investigation, promoted to DECISIONS D21:**
`ASK { C3773 rdfs:subClassOf+ C3809 }` returns `false` even though `C3773`'s own stated
definition includes `C3809` as an intersection member (which entails `C3773 ⊑ C3809`).
Re-verified 2026-07-09: this holds in the **stated graph *and* the inferred default
graph**, and neither carries a direct `rdfs:subClassOf` edge — so there is no graph in
this deployment against which defined-class-to-defined-class subsumption can be read off
`rdfs:subClassOf+` (only role-restriction flattening is materialized).
`filler_selection.py`'s most-specific selection, which relies on `rdfs:subClassOf+`, is
therefore blind to some genuine subsumptions between defined classes. The current curated
projection can therefore over-report co-equal values. D50's materialized complete record
preserves the source facts independently of whether the curated projection collapses
them. This also
caps precision against a single-valued oracle, and it
means §10's `roundtrip_fidelity` **must not** use the inferred graph as its closure oracle
(D21.3); this did not affect the C6135 result above (both `R101` and
`R105` candidates it needed to compare happen to be primitive classes with real
`rdfs:subClassOf+` edges) but is a risk for other concepts and worth keeping in mind if
most-specific selection ever silently under-collapses an axis.

Implementation: `ontolib/src/ontolib/decomposition/walker.py` and
`stated_queries.py` provide the multi-parent DAG walk; the routed extractor and scorer
live in the tracked decomposition package.
Full narrative: this §6 and DECISIONS D14–D20.

### 6.4 R101 anatomy resolution — validated against 4 concepts: real improvement, not a full fix (2026-07-08)

§6.3 found that `C6135`'s 5 tied `R101` candidates resolve to a single leaf (`C12400`
Thyroid Gland) once most-specific selection also treats NCIt's own `R82
Anatomic_Structure_Is_Physical_Part_Of` role as transitive-ancestor evidence, alongside
`rdfs:subClassOf+` (is-a) — **no external Uberon lookup needed** for that case. Before
writing that into this design as settled, it was checked against 3 more concepts:
Left Atrial Myxoma (`C4791`), Stage IIIB Lung Small Cell Carcinoma with Pleural Effusion
AJCC v7 (`C35756`), and Stage III Colon Cancer AJCC v7 (`C89995`). Result: **the
technique generalizes partially, not fully.**

| Concept | Raw R101 candidates | is-a ∪ part-of leaves | Outcome |
|---|---|---|---|
| `C6135` | 5 (Thyroid Gland, Endocrine Gland/System, Head and Neck, Neck) | **1** (Thyroid Gland) | Fully resolved |
| `C89995` | 3 (Colon, Colorectal Region, Digestive System) | 2 (Colon, Colorectal Region) | Partially resolved (system eliminated; organ-vs-region tie remains) |
| `C35756` | 4 (Lung, Bronchus, Endocrine Gland/System) | 3 (Lung, Bronchus, Endocrine Gland) | Partially resolved (Endocrine System eliminated; three unrelated siblings remain) |
| Left Atrial Myxoma (`C4791`) | 7 (Heart, Cardiac Atrium, Left Atrium, Endocardium, Soft Tissue, Thoracic Cavity, Connective/Soft Tissue) | 4 (Left Atrium, Endocardium, Soft Tissue, Thoracic Cavity) | Historical intermediate result. The reviewed projection distinguishes the anatomical kinds and routes them separately: Left Atrium (`C12869`) is `op:PrimarySite`, while Endocardium (`C13004`) is retained as both `op:AssociatedRegion` (from R101 routing) and `op:AssociatedSite` (from R100). |

**What the technique reliably does:** correctly and consistently eliminates candidates
that are genuine is-a or part-of *containers* of another candidate, in every case tested
— it never removed a candidate it shouldn't have.

**What it does not do:** resolve ties between candidates that are simply *not related* to
each other in NCIt's own graph, which turns out to be the common case, not the exception.
Two recurring patterns, neither of which a more complete external anatomy ontology
(Uberon) is obviously the fix for:
- **Region vs. organ:** `Colorectal Region` vs. `Colon`, `Head and Neck`/`Neck` vs.
  `Thyroid Gland` — NCIt models named regions and named organs as siblings under a
  common broader container, not one nested in the other, even where a clinician would
  read one as implying the other.
- **Site vs. cross-cutting system classification:** `Lung`/`Bronchus` (literal site) vs.
  `Endocrine Gland`/`Endocrine System` (asserted because Small Cell Lung Carcinoma is
  classified as a *neuroendocrine* tumor — the same pattern already seen for `R105` on
  `C6135`, now recurring on `R101`). These are genuinely two different, simultaneously
  true, non-nested facts, not a specificity ladder Uberon would linearize.

One case (`C35756`'s `Lung`/`Bronchus`) plausibly *would* resolve via Uberon, since real
anatomical containment exists between them that NCIt's own `R82` graph doesn't capture
(`Bronchus` is asserted part-of `Bronchial Tree` part-of `Respiratory System`; `Lung` is
asserted part-of `Respiratory System` directly — siblings in NCIt, but not in general
anatomical knowledge). That is one plausible win out of four concepts, not evidence
Uberon would close the gap generally.

**Recommendation, revised from §6.3's more optimistic framing:**
1. **Implement the is-a ∪ part-of (`R82`, transitive) extension to `filler_selection.py`'s
   most-specific selection regardless** — it is a real, validated, zero-downside
   improvement (never wrong in 4/4 tests) and reduces `needs_review` noise materially.
   #145's endpoint-bound query preserves one-edge lookup. #213 closes R82-to-R82
   chains with constant-subject one-step expansion instead of an unbound property path:
   at most 8 R82 hops plus a sentinel expansion, 8 inherited named-superclass hops,
   256 cumulatively expanded codes, 16 constant subjects per single-attempt request,
   and 64 such requests. Each query uses `LIMIT 257` and rejects more than 256 rows;
   cumulative accepted response rows above 4,096 or a query body above 65,536 bytes
   also fail closed. The version-pinned `26.06e` contract is
   `C12400 -> C13063 -> C12418`.
2. **Do not expect it to eliminate `needs_review` for R101.** The existing
   `needs_review` flag on a tied leaf set (already part of `filler_selection.py`'s
   design) is the right mechanism for the residual ties — accept some primary-site axes
   as legitimately multi-valued or curator-reviewed, rather than chasing a single
   mechanical answer.
3. **Uberon cross-checking is not validated as the general fix** and should not be
   built as the default plan on the strength of this investigation. If pursued at all,
   scope it narrowly (e.g. only for candidate pairs where NCIt's own graph shows no
   relation at all) and expect a similarly partial result, not full resolution — the
   `Lung`/`Bronchus` case is the one example here that looks promising, not four.

Research code: local, untracked scratch. Full narrative: this §6 and DECISIONS D16.

### 6.5 Finding: the residual ambiguity is role-sense conflation, not a gap in NCIt's atomic vocabulary (2026-07-08)

§6.4's residual `R101` ties, and D15's `R105` finding, both raise a deeper question worth
answering precisely: **do NCIt's existing simple (non-pre-coordinated) concepts fully
cover the semantics of its pre-coordinated concepts, or would decomposing into that
existing pool leave real gaps?**

**Answer: the atomic pool is not the gap.** Every filler encountered across all four §6.4
concepts (`Thyroid Gland`, `Endocrine Gland`, `Colorectal Region`, `Lung`, `Bronchus`,
`Endocardium`, …) was checked and confirmed **primitive** — none is itself a secretly
pre-coordinated (defined) class. There is no case in this investigation where a needed
concept doesn't already exist.

There are two distinct, real phenomena behind the residual ties, and neither is a
vocabulary gap:

1. **A defined class's `owl:equivalentClass` chain is, by construction, an exact,
   lossless definition in terms of existing primitives and roles.** Fully unfolding it
   (every branch, every role, nothing dropped, multi-valued axes kept multi-valued) is
   *always* achievable and *always* exact — that's definitionally what the pre-coordinated
   concept's semantics already are. **The only reason decomposition can lose fidelity is
   that this project deliberately simplifies**: a curated projectable-role policy and
   specificity collapse for genuinely nested candidates, while preserving unresolved
   co-equal fillers. That simplification is the right trade-off for
   producing something a curator can read — but it is a chosen trade-off, not a discovery
   about NCIt's expressiveness.
2. **NCIt's role vocabulary is coarser than a clean single-valued axis model needs.**
   `R101 Disease_Has_Primary_Anatomic_Site` is reused for two pragmatically different
   things: the literal organ (`Thyroid Gland`, `Lung`), and a broader
   histologic/lineage-classification association inherited from a tumor-family ancestor
   (`Endocrine Gland`/`Endocrine System`, because the concept is *also* a neuroendocrine
   tumor). **Confirmed, not hypothesized:** the exact same generic ancestor concept —
   `C3010 "Endocrine Neoplasm"` — is reached in *both* `C6135`'s (thyroid) and
   `C35756`'s (lung) genus DAGs, asserting the identical `R101 → Endocrine Gland/System`
   restriction as part of its own organ-agnostic definition in both cases. This is a
   systematic, corpus-level pattern (a small set of reusable, organ-agnostic "lineage"
   ancestors), not a one-off coincidence — and it is a genuine structural property of
   *how NCIt uses its roles*, distinct from the atomic concepts themselves.

Prior art for exactly this tension: SNOMED CT's concept model hits the same problem (a
concept can have several true, non-nested values for nominally one attribute type) and
resolves it with **relationship groups** — multiple grouped sets of attribute-value pairs
per concept, rather than forcing one value or flattening everything. **Adopted (DECISIONS
D19)** as this project's target axis model, over either "pick one" (loses information) or
"keep everything" (isn't a decomposition anymore). This is the mechanism that reconciles
D15's most-specific rule with README goal 4: most-specific collapse is correct **only**
within a nested (is-a/part-of) candidate set, where the coarser fact stays derivable by
subsumption; genuinely co-equal, non-nested values (site vs. lineage, organ vs. region) are
kept as distinct facts and never collapsed. Selected routed region/stage axes may receive
synthetic groups, while lineage classifiers remain ungrouped. D50 materializes the complete
representation and makes the single-valued projection derived and traceable rather than
a replacement (§6.6, D19).

### 6.6 Strategy: classify role-bearing *genus concepts* by sense, additively, before axis assignment — validated direction, refined from a stronger initial proposal

The natural response to §6.5 is to stop trying to disambiguate a role's sense per-node,
ad hoc, and instead classify **the relation itself** wherever it's overloaded — resolving
the sense once, upstream of node decomposition, rather than re-deriving it every time a
descendant concept is decomposed. The initial shape of this proposal was to
mechanically split an overloaded role into multiple single-sense roles and regenerate the
graph with the split roles before doing any node-level work. Investigating the concrete
mechanism changes the *implementation shape*, not the direction:

**What doesn't work as a general classifier:** the filler's own NCIt semantic type. It
correctly separates some residual ties (`Colon` "Body Part, Organ, or Organ Component" vs
`Colorectal Region` "Anatomical Structure"; `Left Atrium` "Body Part, Organ, or Organ
Component" vs `Endocardium` "Tissue") but **not** the case that matters most: `Lung` and
`Endocrine Gland` are *both* typed "Body Part, Organ, or Organ Component" in NCIt, despite
one being the intended site and the other a lineage artifact. Semantic type alone cannot
distinguish "wrong organ from an unrelated lineage-classification branch" from "right
organ", because both are, mechanically, organs.

**What does work, confirmed against 2 independent concepts:** classifying the **genus
concept that anchors the restriction** — not the raw triple, not the filler — by whether
it is *site-specific* (named for an organ/region, narrow relevance) or
*lineage/histology-generic* (organ-agnostic, e.g. `Endocrine Neoplasm`, and by the same
pattern presumably `Sarcoma`, `Carcinoma` superclass concepts generally). This is exactly
the level-by-level DAG walk D14 already performs (`walk_intersection.py`'s `Level`
already records which genus each restriction was found on) — the sense classification is
additional metadata on a node *already visited*, not a new traversal.

**Revised strategy (additive, incremental, not a global rewrite):**
1. Classify **genus concepts**, not triples, and only the ones that actually anchor a
   `R88`/`R101`/`R105`-type restriction somewhere in a decomposition-scope concept's DAG —
   a few hundred to low thousands of concepts, not a pass over NCIt's full ~200K classes
   or its millions of triples.
2. Persist the classification **additively** (a new `op:` annotation on the genus concept,
   or a lookup table alongside the golden set) — never rewrite or relabel the existing
   `R101`/`R105` triples themselves. This matches the project's existing additive/
   non-destructive principle (README goal 2) rather than introducing a second kind of mutation
   risk alongside it.
3. During per-level role extraction, consult that classification to route a restriction to
   its univocal `op:` axis (site-specific / default) or a contextual `op:` axis such as
   `op:AssociatedLineageClassification` (lineage-generic) — the same pattern already used
   for `R88`'s stage-value/stage-system split, generalized from a per-filler label check to
   a per-genus classification lookup.
4. Start from the concepts already confirmed lineage-generic (`C3010` Endocrine Neoplasm,
   `C3809` Neuroendocrine Neoplasm, `C3773` Neuroendocrine Carcinoma) and expand via the
   same golden-set/precision-recall methodology already established for D14/D15, rather
   than attempting a one-shot global classification pass.

**Resolved (2026-07-08, DECISIONS D20): R101 gets two independent, composable refinements,
applied in order** — not one. The region-vs-organ ties (`Colon`/`Colorectal Region`,
Left Atrium (`C12869`)/Endocardium (`C13004`) are a *second*, distinct phenomenon from
lineage conflation: Left Atrium is an organ chamber and Endocardium is tissue, so the
projection routes these unlike anatomical kinds separately and retains Endocardium as
both `op:AssociatedRegion` and `op:AssociatedSite` where the source roles support them.
They aren't reached through a reusable, organ-agnostic ancestor the way `Endocrine Gland`
is. The resolution:

1. **Genus-sense classification (this section, D17)** runs **first** and routes
   lineage-generic restrictions to `op:AssociatedLineageClassification`, removing the
   `Endocrine Gland`/`Endocrine System` artifacts from `R101` at their source.
2. **Filler-semantic-type ranking** then orders the *remaining* non-lineage ties, using
   the signal §6.6 confirmed separates them — `Colon` "Body Part, Organ, or Organ
   Component" vs. `Colorectal Region` "Anatomical Structure"; `Left Atrium` organ vs.
   `Endocardium` "Tissue". The organ-level filler is the `R101` primary site; the
   co-present region/tissue is routed to `op:AssociatedRegion`.

Order matters because semantic type *fails* on the lineage case (both `Lung` and `Endocrine
Gland` type as "…Organ…") — which is exactly why (1) must carve off the lineage sense before
(2) is applied. The curated projection can report one primary site and serialize supplied
groups, while the D50 complete record preserves every stated site and associated
region/lineage fact even when projection policy omits it. Both refinements are additive
(new `op:` axes, never rewriting `R101` triples).
The D23 morphology-to-organ lookup selects the primary organ but does not bypass this
split: region/tissue fillers are taken from the pre-collapse R101 set and routed to
`op:AssociatedRegion`, so an R82 whole/part comparison never deletes a fact merely
because it belongs on a different normalized axis (#156).

The resulting `Decomposition` model enforces `op:PrimarySite 0..1` for resolved
constituents; this is not a total relation.
No-site classes are not thereby cancers of unknown primary, and neither a Finding such as
`C48322` nor a minted "unknown anatomy" sentinel may enter the anatomy-valued axis. D58
defines a separate future occurrence-level `primarySiteStatus` with values `known`,
`unknown-cup`, `undetermined`, and `not-applicable`. No current extractor field computes or
persists that status. D58 specifies a future class-level derivation in which a site means
`known`, explicit unknown-primary evidence means `unknown-cup`, and otherwise no site means
`not-applicable`; `undetermined` requires patient-level workup state. A future class-level
`not-applicable` summary MUST NOT propagate to an occurrence; a solid-tumour occurrence
from a site-agnostic class starts `undetermined` until occurrence evidence establishes a
site or CUP state.
Occurrence individuation is upstream: this cardinality must never turn a possible second
primary into a metastasis merely to satisfy the projection.
Issue #263 owns the future occurrence/status implementation; it does not change this
class extractor or the issue #57 class-level pair oracle.
Validate via the D14/D15/D17 golden-set methodology.

The certified 26.07d audit extended inherited production projection to R103 and R108.
Their recurrent generic fillers are suppressed only through
`contracted-role-generic-v2`. R103 is source-annotated as non-defining and the contract
propagates that modality without removing its 12 expectations from scoring;
`ncit-26.07d-unsupported-filler-v1` excludes C54105 for the two concepts whose definitions
contradict that filler. The 10 R104 and one R107 survivors stay held until axis
adjudication and remain visible as complete-record scope omissions. Filler selection
receives is-a and R82 as separate predicates: is-a licenses collapse on routed axes except
`op:AssociatedLineageClassification`, whereas R82 licenses collapse only for location
axes. `ncit-26.07d-stage-kind-v1` routes R88 systems/frameworks through a reviewed code
allowlist; definitions informed curation but are not consulted at runtime because semantic
type does not separate systems from values (D59).

Full narrative and the confirmed-shared-ancestor evidence: this §6 and DECISIONS D17.

---

## 7. NLP fallback + minting (`nlp_fallback.py`, `minting.py`)

For axes that live only in the label (assessment §3.4): laterality (Left/Right/Bilateral), staging-manual version where not carried as a stage-system filler, and "with/without `<finding>`" negation.

### 7.1 Extraction
Rule/pattern-based (not a model) for determinism and testability: a small typed grammar over the preferred label + synonyms emitting `AspectRecord(axis, surface_form, polarity)`. Laterality and "with/without" are the primary yields; the finding concept itself usually already exists as a role filler, so only the *negation*/qualifier is new.

### 7.2 Minting (proposals, never silent creation)
When an NLP aspect has no existing NCIt concept (e.g. an explicit *absent/excluded* qualifier, or a laterality value not modelled), emit a **deterministic** proposal:

- **Stable synthetic id:** `op:MINT-{sha1(axis + '|' + normalized_label)[:12]}` — same input always yields the same id, so reruns are idempotent and diffable.
- Persisted first in the run-scoped `decomp_minted_proposal` table with
  `status='proposed'` + `source_signal`, and linked in that run's additive graph exactly
  like any other constituent (`op:filler op:MINT-…`). Only successful run completion
  promotes it into the global `minted_concept` curator queue (D48).
- The mint tail is expected to be **low hundreds**, concentrated in qualifier/value-set nodes, not clinical entities (assessment §3.4).

`test_missing_constituent_minting` pins that an NLP-only aspect produces a proposal record — never a silent create.

---

## 8. Legacy writer (`legacy_writer.py`)

Pure function: `(source_code, list[Constituent], run) → RDF triples`. Emits only the
additive §4.2 vocabulary into a durable staging file; D53's publisher validates and
promotes it to `DECOMPOSED_GRAPH_IRI`. The writer emits no `DELETE`, names no graph, and
emits no `owl:equivalentClass`. A reserved equivalence request is rejected before stdout
or filesystem effects.

---

## 9. Run orchestration & CLI (`run.py`)

```
pdm run decompose \
  --source-manifest /path/to/.ontoprism-ncit-candidate.json \
  --branch neoplasm --out data/ncit_decomposed.ttl [--load] [--resume RUN_ID]

pdm run decompose \
  --source-manifest /path/to/.ontoprism-ncit-candidate.json \
  --branch neoplasm --sample-manifest samples/ncit-26.07d-m1-review.json \
  --out data/ncit-26.07d-m1-review.ttl
```

The required D47 candidate manifest is revalidated and its full live-endpoint observation
must match before work begins. A fresh run enumerates the scope once, persists the exact
ordered worklist and immutable fingerprint, then processes every item, including
zero-output concepts. Resume never re-enumerates: it validates all caller-controlled
fingerprint dimensions and processes exactly unfinished items. Metrics and a normalized
TTL are reconstructed from the full persisted run, making fresh and resumed execution
equivalent. Source identity is rechecked before publication; drift fails the run and
invalidates every persisted result row. The `--out` TTL is rendered to a durable staging
sibling and validated against the exact run. With `--load`, a unique staging graph is
transactionally promoted with its source/representation marker; the file is then
atomically replaced and directory-synced. Only after both requested publications succeed
is the run complete. Failures after publication intent is journaled but before completion
remain separately visible and retryable; a matching marker-ahead retry is idempotent.
Preflight failures fail the run, while post-completion lock-release failures only surface
cleanup failure (D53).

`--emit-equivalence` remains a reserved proof-emission seam for #153 but always refuses
before configuration loads or clients are constructed.

`--branch neoplasm` materializes strict descendants of `C3262`;
`--branch disease` materializes strict descendants of `C2991`, including `C3262` and
its descendants. Both use `stated-genus-subclass-v1` and the axis-qualified algorithm.
Changing a root or scope algorithm version invalidates resume just like a source change.

The optional D55 sample manifest replaces hierarchy-order truncation with an explicit
source-bound review worklist. It is validated before provenance: branch/root/scope,
source identity and ontology version must match, and every selected code must occur in
the freshly enumerated hierarchy closure. Its exact order and canonical digest are part
of schema-v3 run/resume identity. Sample execution is file-only: it requires `--out` and
is mutually exclusive with `--total-limit`, `--load`, and equivalence emission. The
tracked 26.07d M1 sample covers the applicable and excluded semantic-type paths, deep
genus/morphology, every observed staging family, multi-parent and grouped/multi-value
definitions, NLP/mint, region/organ, known-hard, and atomic/no-op cases.

**Source pinning:** the run records both `owl:versionInfo` and D47's stable source
identity, which binds the exact stated/inferred artifact pair, loader, layout, policy,
and candidate observation. Version or identity mismatch fails closed.

---

## 10. Quality / coverage metrics

| Metric | Status | Definition |
|---|---|---|
| `pct_decomposed` | stored in `decomp_run.metrics` | cumulative decomposed concepts / exact persisted worklist size; identical after fresh or resumed completion |
| `decomposed`, `residual`, `semantic_excluded`, `atomic_noop`, `unknown_outcome` | stored in `decomp_run.metrics` | closed D56 work-item classifications; a completed current run reconciles these exactly to `total_in_scope`, while `unknown_outcome` identifies migrated history whose original reason cannot be recovered |
| `residual_precoordination`, `residual_precoordinated_count` | stored together in `decomp_run.metrics` | decomposed concepts with at least one emitted constituent that the same detector classifies as pre-coordinated, divided by all decomposed concepts (D37); storing numerator and rate gives fresh, resumed, CLI, and API reads one schema |
| `minted_count` | stored in `decomp_run.metrics` | size of the mint tail (governance signal — should stay low hundreds) |
| `roundtrip_fidelity` | unavailable (`null`) for new runs | a future proof/validation step may measure this only from D50's complete representation, never from the curated projection; historical numeric values remain readable |
| `constituent_existence_rate` | future | fillers resolving to an existing active concept / all fillers (target ≈100% on roles path) |
| `complete_definition_count`, `complete_fact_count` | stored in `decomp_run.metrics` | decompositions with a complete record and total stated definition facts |
| `projected_fact_count`, `projection_loss_count`, `projection_loss_rate` | stored in `decomp_run.metrics` | distinct complete facts referenced by the curated view, omitted fact count, and omitted / complete ratio |
| `needs_review_count` | future | ambiguous anatomy / multi-filler axes flagged for curation |

The inferred default graph may validate constituent existence even though extraction never reads from it. Projection loss is measured against the stated complete record; no round-trip closure or fidelity is claimed. Future fidelity is a property of the **complete** representation (D19/D50); the curated projection is not expected to round-trip.

---

## 11. Test-driven build plan

Strict TDD (repo standard): failing test → minimum code → green → ruff + basedpyright clean → commit. RED tests, with fixtures captured from the running stated store:

- `test_hierarchy_scope` — the disease closure contains the neoplasm closure; defined
  classes such as `C9305`, `C2916`, and `C6135` are reached through named genus edges;
  unrelated `C12400` is rejected; cycles and duplicate edges terminate deterministically.
- `test_hierarchy_scope_source_contract` — rebuild the cached, same-release-bound NCIt
  pair into a certified inactive sibling and prove the hierarchy contract against that
  exact source identity. It also proves the tracked D55 sample is bound to that source
  and every selected code is in the certified neoplasm hierarchy. This explicit
  `full_build` test is excluded from automatic suites.
- `test_detect_precoordination` — `C6135` (≥2 roles, supported type) flagged; `C12400` (Thyroid Gland, atomic) not; a hierarchy member with an unsupported type fails the algorithm-applicability gate.
- `test_extract_constituents_roles_first` — `C6135` → {stage C27970, stage-system C90530, primary-site C12400, abnormal-cell C36761, morphology-from-parent}; `Excludes_*` filtered; most-specific filler chosen over ancestors.
- `test_constituent_existence` — every roles-path constituent resolves to an existing active `owl:Class` (≈100%).
- `test_most_specific_filler` — given an axis result set {Thyroid Gland, Endocrine Gland, …Neck}, selection returns only Thyroid Gland.
- `test_nlp_fallback_laterality` — `C4791` (Left Atrial Myxoma) → laterality=Left recovered from label; emits a needs-qualifier record.
- `test_missing_constituent_minting` — NLP-only aspect with no concept → deterministic proposal (stable id + provenance), not a silent create; rerun yields the same id.
- `test_legacy_representation` — decomposing `C6135` leaves the original intact and adds `representationStatus="legacy-precoordinated"` + `hasConstituent` triples in the decomposed graph only; the original code still resolves.
- `test_additive_no_deletions` — OWL-diff of the stated + inferred graphs before/after a run is empty (structural additivity guarantee).
- `test_equivalence_request_fails_closed` — CLI, programmatic run configuration, and direct writer requests refuse before clients or artifact effects; accepted output remains byte-for-byte unchanged.
- **Golden-file test** — a curated ~200-concept neoplasm sample → expected constituent JSON; CI diff-gates the golden output (this is the assessment §7 de-risking spike, promoted into a regression gate).

Integration tests marked `@pytest.mark.integration` run against the live stated graph and are version-pinned.

---

## 12. Phasing & PR cadence

Split M5 into two PRs (matches the plan's 5a/5b split), each `/pr-review-toolkit:review-pr` to zero findings before merge (pre-PR protocol):

- **PR 5a — detect + extract:** `axes.py`, `detector.py`, `stated_queries.py`, `filler_selection.py`, `models.py`, the golden-file spike over ~200 neoplasm concepts. Deliverable: a pure decomposition function + coverage numbers, no writes.
- **PR 5b — write + persist + CLI:** `nlp_fallback.py`, `minting.py`, `constituent_index.py`, `legacy_writer.py`, `provenance.py`, migration `0003_decomposition`, `run.py` + CLI, additivity test, run manifest. Deliverable: `ncit_decomposed.ttl` + `decomp_run` for the neoplasm branch.

The #9 read surface is implemented through the concept decomposition API, frontend API
client, and `DecompositionPanel`; it reads only the published decomposition graph.

---

## 13. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Inferred-vs-stated confusion (ancestor bleed, `Excludes_*`) | Extract from the **stated** graph only; most-specific selection as defense-in-depth; inferred used solely as validation oracle |
| Most-specific errors on multi-parent anatomy | NCIt-hierarchy (is-a + `R82` part-of) cross-check, validated §6.4 as a real-but-partial fix; ambiguous cases flagged `needs_review`, not silently resolved; Uberon not validated as a general fix |
| Semantic loss on "without/excludes" | Model absence explicitly as a minted qualifier + `polarity`; never drop the negation |
| Consumer breakage | Stated and inferred NCIt remain untouched (`test_additive_no_deletions`); the output graph is marker-guarded and atomically replaced as one complete publication |
| Scope drift or semantic-type/hierarchy conflation | Rooted stated-DAG closure is fingerprinted; semantic type remains an explicit algorithm-applicability gate |
| NCIt version bump silently changes roles | Version-pinned run manifest + guard test that fails on a build mismatch |
| A deep filler superclass cone exhausts R82 safety bounds | Constant-subject closure permits the certified C27262 minimum of 14 inherited-superclass hops while retaining the independent 8-hop R82, 64-request, 256-code, and 4,096-row caps (D54) |

---

## 14. Resolved decisions

The decisions flagged during design (plus one that emerged from implementation) are
resolved below (grounded in the assessment data, the code, and the issue tracker). Each
records the call and the rationale.

1. **`min_defining_roles` — keep default 2, but gate on ≥2 *decomposable axes*, not raw roles.**
   First, a correction: **55,044 is the corpus-wide count** (all 204K classes, incl. the out-of-scope gene/protein families). It must **not** be used as a branch population figure. The hierarchy root selects the population, then the semantic-type applicability gate (§5.1) selects concepts handled by the axis-qualified algorithm. Reports must expose both the hierarchy worklist and post-gate results rather than presenting the canonical three-type total as branch scope.
   Second, the gate itself: count **decomposable axes** = stated defining roles **+** morphology-from-parent (§6) **+** label-signalled axes (`label_multi_aspect`, §5). A concept with a single site role but a morphology-bearing taxonomic parent is genuinely 2-axis (site + morphology) and must qualify; a raw `role_count ≥ 2` test would wrongly drop it, while truly single-axis nodes (one role, atomic parent, no label signal) are still excluded. Config key stays `min_defining_roles` (default 2) for the role component; the axis-count framing is the detector's actual predicate.

2. **Regimen branch — deferred, and it needs its own mini-design (not just a later run).**
   `Chemotherapy_Regimen_Has_Component` (14,121 axioms) is **mereological** — a regimen *has drug components* — not the site/morphology/stage **axis** model this engine is built around. It does not fit the axis catalogue, most-specific-filler selection, or morphology-from-parent machinery, so folding it into 5a/5b would force two different decomposition semantics into `axes.py`/`filler_selection.py`. Keep it out of the first pass; when it lands it gets its own small design and a distinct `--branch regimen` decomposition kind. Neoplasm + disease first. **Mini-design:** [NCIt regimen decomposition](./ncit-regimen-decomposition.md) (PR 5c).

3. **Vocabulary namespace — `https://w3id.org/ontoprism/vocab#` (prefix `op:`).**
   Nothing in-repo pins `ontoprism.org`; the only canonical identifier is `github.com/hniedner/ontoprism`. A **w3id.org persistent identifier** is the right choice: it is community-standard for linked-data/OBO vocabularies, is made resolvable via a one-line redirect PR to the w3id registry, and does **not** depend on owning (or keeping) the `ontoprism.org` domain — matching the repo's existing use of a purl persistent identifier for `UBERON_NS` (`namespaces.py`). Set `ONTOPRISM_NS = "https://w3id.org/ontoprism/vocab#"`. *Only* switch to `https://ontoprism.org/vocab#` if that domain is actually owned and committed to long-term; a namespace IRI need not resolve to be valid, but a stable, controllable one avoids a future migration of every `op:` triple.

4. **`owl:equivalentClass` emission — reserved and fail-closed pending proof validation.**
   Issue **#6 ("Post-coordination expression syntax for observations & findings")** still owns the *user-facing post-coordination grammar*. D50 provides the complete structural record that must precede any exact axiom or fidelity measurement, but does not by itself prove equivalence. The CLI flag remains reserved and rejects every request before effects (D43); the curated projection cannot authorize equivalence.

5. **Most-specific filler selection applies *across alternate DAG branches*, not just within one branch's collected candidates. Resolved 2026-07-08 — see DECISIONS D14/D15 and §6.3.**
   §6.2 originally recorded `C6135`'s `R105` axis resolving to `C36825` (one level more specific than the assessment's expected `C36761`) as a bug ("the wrong constituent"). It is not: `C36825` and `C36761` are both genuinely stated, on different multi-inheritance branches of the same DAG, and `C36825 ⊑ C36761` — i.e. both are simultaneously true, and something must decide which one a single-valued axis reports. Decision: prefer the most-specific, per SNOMED CT's Necessary Normal Form precedent (production algorithm, decades of use, same multi-parent-DAG problem class) and the peer-reviewed normal-forms literature it implements (Spackman 2001, PMID 11825261) — full citations in D15. This also serves this project's own round-trip-fidelity goal (§10): the specific filler is needed to exactly reconstruct the original concept; the coarser one only reconstructs an ancestor. Nothing is lost by preferring the specific fact — the coarser one remains derivable via ordinary subsumption. **Scope-corrected by decision 6 below:** this "nothing is lost" reasoning holds only for *nested* (is-a/part-of) candidate sets; non-nested co-equal values must not be collapsed.

6. **The structural representation of record is the complete unfolding; the single-valued view is a lossy curated projection. Resolved 2026-07-08 and materialized by D50 — see DECISIONS D19/D50 and §6.5.**
   Because README goal 4 requires eventual round-tripping, the single-valued/allowlist output cannot be the artifact of record. Decision: the complete multi-parent-DAG record contains all stated named genera and existential restrictions, including non-defining and excluded roles, with source-expression groups. D50 materializes it and makes the single-most-specific, allowlist-filtered output explicitly derived and traceable. This structural record may support a future reversibility proof, but exactness, equivalence, and round-trip fidelity remain unproven and quarantined pending separate validation.

7. **`R101` primary-site disambiguation uses two independent, composable refinements. Resolved 2026-07-08 — see DECISIONS D20 and §6.6.**
   D17 left open whether the region-vs-organ ties (`Colon`/`Colorectal Region`, `Left Atrium`/`Endocardium`) need a second mechanism beyond genus-sense classification. They do. Decision: (1) genus-sense classification (D17) runs first and routes lineage-generic restrictions to `op:AssociatedLineageClassification`, then (2) filler-semantic-type ranking orders the residual non-lineage ties (organ-level "Body Part, Organ, or Organ Component" wins the `R101` site; region/tissue is routed to `op:AssociatedRegion`). Order matters — semantic type fails on the lineage case both fillers type as organs, so (1) must carve off lineage before (2). Both additive; under decision 6's groups model each tie becomes distinct grouped facts rather than a forced single value.
