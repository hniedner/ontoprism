# ONTOPRISM — roadmap to complete the vision

Working plan, not design-of-record. The design-of-record for the decomposition **engine**
already exists and is authoritative: [`docs/design/`](design/) (assessment → engine
→ regimen) plus the running [`docs/DECISIONS.md`](DECISIONS.md) log. This document
does **not** duplicate that design. It exists to tie together (a) the full 4-goal vision in
`README.md`, (b) the GitHub issue graph, and (c) the actual state of the code/PRs — updated
here rather than left to drift a second time. Promote a decision below into
`docs/DECISIONS.md` once it's actually made; this file stays disposable scratch.

**Currently-open issues** span the three unbuilt README goals and the external-integration
epic: #4/#5/#6 (goals), #9/#57/#61/#62 (decomposition + frontend), #18 (tracking), and
the epic #70. Phase-A children #71/#74/#76 and Phase B–C children #77/#81 are **merged and
closed**. **#73** (validation-driven promotion — the only thing that promotes `closeMatch` to
`exactMatch`, and so the only thing that moves `COV` off ~0) and **#72** (its `candidate_recall`
baseline is still unrecorded) were **reopened on 2026-07-12** after a tracker-vs-code audit.
Phase B–E remaining are #75/#78/#79/#80/#82/#83/#84.
#5 and #6 are deliberately *not*
designed yet — see §3 — that is the correct sequencing per their dependency on #4, not a gap.

**2026-07-11 strategy shift (§5):** a new design-of-record,
[`docs/design/ncit-external-integration.md`](design/ncit-external-integration.md) (DECISIONS
D24–D29), reframes the endgame: **NCIt becomes the oncology-specific specialization of a vetted
upstream substrate** (Uberon / Cell Ontology / SNOMED CT + ICD-O-3 / Mondo), joined by a
**dual-canonical, additive mapping layer** that preserves NCIt *and* caDSR anchoring. This adds a
parallel enabling track — the external-integration epic **#70** (children **#71–#84**) — that gates
goal-4 interoperability. It does **not** re-order the existing critical path or jump ahead of #44's
relations-before-coverage rule (D22). The design was **hardened by a peer-reviewed literature pass
and an adversarial red-team** (D27–D29, design §14): the caDSR guarantee is now *enumerate-then-measure*
(the ~20K role-target atoms are **not** the set caDSR anchors to — value-domain grade/laterality
concepts and out-of-scope CDEs must be enumerated and mapped explicitly), mapping validation is
non-circular + EL-profiled + reasoner-committed, and mapping economics/licensing/rot are corrected.

## 1. Vision recap and status (README goals)

| # | Goal | Status | Tracking |
|---|---|---|---|
| 1 | Rich explorer over NCIt + caDSR | **Done** | Closed: #1,#2,#3,#7,#8,#10,#11,#12,#13,#14,#15,#16,#17 |
| 2 | Decomposed ("atomic") NCIt | **Engine + SME golden-set curation loop landed (#45/#46/#47/#58/#59/#44 closed); golden-set expansion (#57) ongoing** — see §2 | #4, #9, #57, #60, #61, #62 |
| 3 | Balanced concept graph | **Not started, correctly deferred** — see §3 | #5 |
| 4 | Post-coordination expression syntax | **Not started**, but D19 puts its `--emit-equivalence` seam on goal 2's critical path — see §3; D24–D26 now bind its ranges to the upstream substrate — see §5 | #6 |
| 5 | NCIt as a specialization of the OBO/SNOMED substrate (dual-canonical) | **Phase A–B children #71, #74, #76, #77, #81 merged & closed; #73 (validation-driven promotion) + #72 (recall baseline) reopened 2026-07-12; design-of-record + peer-review/red-team hardened (D24–D29); Phase C–E pending** — parallel enabling track — see §5 | #70 (#72, #73, #75, #78–#80, #82–#84) |

Critical path (per #18): Phase 0 ✅ → Phase 1 ✅ → **#4 → #9 → #5 → #6**, with the
external-integration track (#70/#71–#84) running **parallel to #44** and feeding goal 4 (§5). #9's read/serve
surface already landed ahead of #4's writer (PRs #41/#42). #4's writer/orchestrator/CLI
landed in PR #45 (merged), the role-sense-conflation findings in PR #46 (merged), and
automated semantic versioning in PR #47 (merged; DECISIONS D18 — releases now cut on merge
to main, **currently at v0.8.1**) — see §2.

## 2. Issue #4 (+ #44, #9, #18) — current state

**PR #45 — "Decomposition 5b: run orchestrator, CLI, and provenance hardening"** is
**merged** to `main` (was branch `feat/decomposition-pipeline`), all 4 CI jobs green
(quality, backend, integration, web), reviewed via a 5-pass agent review with every finding
fixed. Two follow-on PRs also merged: **#46** recorded the role-sense-conflation findings
(D17), and **#47** added automated semantic versioning (D18). It closed out the concrete
gaps this plan flagged on 2026-07-08 before the PR existed:

| Then (pre-PR) | Now (in PR #45) |
|---|---|
| Migration `0003_decomposition.py` had a typo revision id | Fixed + regression test |
| `constituent_index.py` did not exist | Implemented — resolves NLP aspects or mints |
| `run_pipeline()` was a stub returning empty metrics | Real orchestrator: detect→extract→select→NLP→mint→write→persist |
| No `decompose` CLI registered | `scripts/decompose.py` + `pdm run decompose` |
| No additivity test | Structural unit test + live-store round-trip integration test |
| Branch unpushed, no PR | Merged (#45) |

Additional hardening found during review and fixed in the same PR (not anticipated by the
prior version of this plan): a governance bug where re-minting a proposal would silently
overwrite a curator's approve/reject decision (`ON CONFLICT ... DO UPDATE` → `DO NOTHING`);
an NCIt version-pin guard on `--resume` (design §9/§13's own risk mitigation, previously
unimplemented); `--load`/`--out` CLI validation ran *after* a full pipeline run instead of
before; two real Turtle-syntax bugs in `legacy_writer.py` (caught by the new integration
test); several docstring inaccuracies.

### 2.1 Next actions for #4/#44/#9/#18, in order

1. **PR #45 is merged** (done — along with #46 and #47). The decomposition writer/
   orchestrator/CLI is on `main`; the remaining #4 work is extractor curation (#44), not
   the pipeline scaffolding.
2. **#44 research is underway — real progress, not yet done.** A naive full-corpus
   baseline run was executed (branch `neoplasm`, no limit): `in_scope=28967
   decomposed=939 residual=0 minted=87 coverage=3.24%` — real data, but from the known-
   naive flat-restriction extractor, not the fix below; kept as a labeled local baseline in
   Postgres, not the target dataset.
   Separately, the boundary-heuristic research (design §6.2's own scoped workstream) has
   produced two resolved decisions — **DECISIONS D14** (the stated pre-coordination
   hierarchy is a multi-parent DAG, not a linear chain — a naive single-parent walk
   silently drops real content) and **D15** (most-specific filler selection is correct
   *policy* even across alternate DAG branches, with peer-reviewed + SNOMED CT production
   precedent — see `docs/DECISIONS.md` and `docs/design/ncit-decomposition-engine.md` §6.3)
   — plus a measured result on C6135: a defining-axis-filtered, full-DAG walk takes
   precision 0.10→0.31 and recall 0.75→**1.00** versus the original naive baseline. The
   R101 (primary site) hypothesis was then validated against 3 more concepts before
   writing it up as settled (**DECISIONS D16**, `docs/design/ncit-decomposition-engine.md`
   §6.4): is-a + `R82` part-of (transitive) is a real, zero-downside improvement, but
   only fully resolves the tie in 1 of 4 concepts tested — the other 3 have recurring
   region-vs-organ or site-vs-tumor-lineage ties NCIt's own graph simply doesn't relate.
   **Uberon cross-checking is explicitly not recommended as the default next step** (D16)
   — only one sub-case out of four looked like a plausible genuine win for it.
   A deeper question — does this mean NCIt's atomic concepts are insufficient to
   represent pre-coordinated concepts' full semantics? — was investigated and answered
   **no** (**DECISIONS D17**, `docs/design/ncit-decomposition-engine.md` §6.5/§6.6):
   every filler checked is a genuine primitive concept; the real issue is (a) this
   project's own axis-allowlist/single-valued simplification being lossy by design
   (full defined-class unfolding is always exact), and (b) NCIt's role vocabulary
   conflating distinct senses under one role code (`R101` reused for literal site *and*
   lineage-classification — confirmed via the identical ancestor `C3010` anchoring the
   same restriction in both the thyroid and lung test concepts). A refined,
   evidence-based strategy for this (classify anchoring *genus concepts* by sense,
   additively — not a global role-splitting rewrite) is recorded in D17. Two further
   decisions then closed the last open items (DECISIONS D19/D20): **D19** makes the
   complete lossless `owl:equivalentClass` unfolding — multi-valued axes kept as
   SNOMED-style relationship groups — the reversible representation of record, and recasts
   the single most-specific view as an explicitly-lossy curated projection (most-specific
   collapse restricted to *nested* candidates only); **D20** commits `R101`'s two composable
   refinements (genus-sense classification first, filler-semantic-type ranking second).
   Full narrative: DECISIONS D14–D20. Remaining before #44 can graduate: update the golden
   set's C6135 entry per D15, expand the golden set to more concepts, implement the
   validated is-a/part-of extension in `filler_selection.py`, prototype D20's two R101
   refinements + D17's genus classification for the confirmed lineage-generic ancestors,
   and stand up D19's complete-representation layer behind `--emit-equivalence` (the
   round-trip artifact of record) with the single-valued output derived from it.
3. **Once #44 crosses its ≥0.9 threshold**, graduate the extractor from the local research
   spike into `ontolib/decomposition/` (issue #44's own acceptance criteria) and wire it into
   `_decompose_one` in place of the current flat query — this is the one place PR #45's
   own module docstring says a future extractor swap needs real changes, not a drop-in.
4. **Re-run `pdm run decompose --branch neoplasm`** once #44's extractor graduates, to
   replace step 2's naive baseline with the real dataset — this is the point #9's
   already-built read/serve UI gets *trustworthy* data, and where design §12's "PR 5b" is
   truly, fully done (not just code-complete).
5. ~~Update #18 with a progress comment once PR #45 merges.~~ **Done** — progress
   comments posted on #4, #9 and #18 (2026-07-08).

### 2.2 Not done in PR #45 — documented, not oversights

Morphology-from-parent (design §6, the `op:Morphology` axis) has no query yet; the CLI's
`--emit-equivalence` flag is accepted but inert — and per **D19** it is no longer merely
"reserved for #6": it is the seam through which the lossless representation of record is
asserted, so goal 4's reversibility depends on building it out (design §14.4, D21.3);
`RunConfig.load_to_store` is read by the CLI script directly, not by `run_pipeline`. None
of these block #44 or the first real run — they're independently addressable follow-ups
when their dependency (a morphology query; #6 landing) actually exists.

## 3. Issues #5 and #6 — still correctly un-designed

Both remain checklist-only issues with no `docs/design/` entry. #5 depends on #4's
*output* existing, not just its code landing. **#6 is a partial exception since D19:** its
`--emit-equivalence` seam is how #4's lossless, round-trippable representation of record is
asserted, so goal 4's reversibility depends on that seam being built — #6 is no longer
purely downstream of #4. The *grammar* #6 owns still is. **When that grammar is designed,
it does not start from a blank page (D22):** model it on SNOMED CT's Compositional Grammar
(SCG) for expression syntax, its Machine-Readable Concept Model (MRCM) for *sanctioning*
valid refinements (the computable descendant of GALEN/GRAIL sanctioning), and its Expression
Constraint Language (ECL) for the query layer — with HL7 FHIR `ConceptMap.$translate` as the
pre-↔post equivalence surface. Rationale and citations:
[`docs/postcoordination-literature-review.md`](postcoordination-literature-review.md) §4.1,
§8.4. A design for graph balancing (#5) or
a post-coordination grammar (#6) written against zero real decomposed concepts would be
guessing. Note the gate is *trustworthy* data, not *any* data: a full-corpus run has already
happened (§2.1 step 2 — `in_scope=28967 decomposed=939 coverage=3.24%`, a labeled local
baseline), but from the naive flat-restriction extractor. §2.1
step 4 is what produces a dataset worth designing against. When
that first real run exists, the next planning action is a `docs/design/ncit-balancing.md`
written *against its actual coverage metrics* — mirroring how the assessment doc was written
against real store queries before the engine was designed.

## 4. Summary — what to do next, in order

1. ~~Review and merge PR #45.~~ **Done** (#45/#46/#47 all merged; latest release **v0.8.1**).
2. Continue #44's curation loop. **Sequencing (D22): relation quality gates coverage** —
   the genus-sense routing that de-overloads `R101`/`R105` into univocal `op:` axes is a
   *precondition* for pushing coverage past the ~3.24% baseline, not a parallel nicety;
   chasing coverage on top of overloaded roles just propagates the conflation. Every `op:`
   axis minted must carry a stated domain/range/definition (OBO Relation Ontology
   discipline), not just a name. **Done:** `score.py` now excludes `needs_review` and
   scores multi-valued axes (#44's DoD was previously unreachable by construction); the
   C6135 golden entry now encodes D19/D20 (`R105 → C36825`, plus
   `op:AssociatedLineageClassification` and `op:AssociatedRegion`). **Remaining:** expand
   the golden set beyond n=1, implement the is-a ∪ `R82` part-of extension in
   `filler_selection.py`, and prototype D20's two R101 refinements + D17's genus-sense
   classification. Note **D21**: precision against a single-valued oracle is capped by
   NCIt's incomplete subsumption closure, not by the boundary heuristic.
3. Stand up D19's complete lossless representation (relationship groups) behind
   `--emit-equivalence` as the round-trip artifact of record; derive the single-valued
   curated view from it. Per **D21.3**, its `roundtrip_fidelity` check must not use the
   inferred graph as the closure oracle. Reserved vocab already in `vocab.py`
   (`op:group`, `op:associatedLineageClassification`, `op:associatedRegion`);
   `Constituent` still has no group field.
4. Graduate the extractor into `ontolib/decomposition/` once #44 clears its threshold.
5. Re-run `pdm run decompose` for the neoplasm branch with the graduated extractor,
   replacing the current naive baseline.
6. Post a progress update on #18.
7. Only then: start design work on #5, then #6.

**Parallel enabling track (§5): the external-integration epic #70 (#71–#84).** Phase A
(xref framework + SSSOM store + named graph #71; open-license Uberon/CL candidate ingest #72;
caDSR anchor-set enumeration #74; ELK/ROBOT validation primitives #73) is **merged**
(PRs #86/#90/#89/#88), as are **#76** (golden mapping set + coverage generator, PR #114),
**#77** (upstream xref on `op:` fillers, PR #115) and **#81** (morphology-from-parent).

A 2026-07-12 audit of the tracker against the tree **reopened two of them**: **#73**, whose
merged code was only the *primitives* (nothing built a merged-EL bridge, applied an evidence
policy, or persisted a promotion — so no candidate had ever been promoted and `COV` was pinned
at ~0), and **#72**, whose measure-first `candidate_recall` baseline was never recorded. The
#73 orchestration (independent-evidence policy + non-circular ELK gate + D29 lifecycle) is the
correctness core of the epic; everything downstream of the mapping store is cosmetic without it.
Phase C–E (#75, #78–#80, #82–#84) remain; they enrich the same `op:` axes #57 curates and do
not consume the decomposition critical path.

## 5. Strategy shift — NCIt as a specialization of the OBO/SNOMED substrate (D24–D26)

Design-of-record: [`docs/design/ncit-external-integration.md`](design/ncit-external-integration.md).
The endgame is reframed: rather than a self-contained silo, **NCIt becomes the oncology-specific
specialization of a vetted upstream substrate** — Uberon (anatomy), Cell Ontology (normal cells),
SNOMED CT + ICD-O-3 (morphology/histology), Mondo/DO (disease genus) — keeping only what is genuinely
its own (oncology combinations, AJCC staging, regimens) and *mapping to* the substrate for the rest.

Realized as a **dual-canonical, additive bridge** (D24): NCIt stays canonical-of-record for everything
that exists today (and for caDSR anchoring); the upstream stack is canonical for new authoring and
interop; a mapping layer (`skos:*Match` + RO relations + FHIR `$translate`, DL-validated per D25/D21)
always joins them. caDSR is never touched and gains upstream reach transitively (design §6). The
oncology concept becomes an OBO cross-product over a Mondo genus and upstream differentia.

**Why it's mostly reuse, not rewrite:** the `op:` axes (D17/D20/D22/D23) already *are* the RO univocal
relations the feedback wants; SNOMED groups (D19), the SCG/ECL/MRCM grammar (D22), and FHIR `$translate`
(D22) are already adopted; the ~20K atoms already exist (100%, assessment §3.2); and the mappings
themselves largely already exist (NCIm CUIs, Mondo/Uberon/CL xrefs). Licensing (D26) keeps SNOMED/ICD-O-3
flag-gated (open Uberon/CL/Mondo carry the default experience). D16 is *revisited, not reversed* (D25):
Uberon returns as an xref/interop target plus a scoped `part_of` tie-break re-test, not as the declined
most-specific-filler default.

**Sequencing (design §9, §13):** Phase A foundation (xref framework + SSSOM #71; Uberon/CL ingest #72;
**caDSR anchor-set enumeration `C_cadsr` #74**; non-circular EL-profiled validation harness #73) — merged,
with **#73 and #72 reopened 2026-07-12** (see above) — ∥ #44 → Phase B bind-to-decomposition
(**upstream on `op:` fillers #77 ✅ closed**,
Mondo genus #79, Uberon tie-break spike #78) → Phase C morphology + licensing (NCIm SNOMED/ICD-O-3 #80,
**morphology-from-parent #81 ✅ closed**, **value/qualifier mapping #75**) → Phase D serve/interop (`/mappings`,
`$translate`, frontend #82, **published caDSR coverage report #83**) → Phase E grammar (#84, folds into #6).
The **caDSR coverage number is the artifact that proves the mapping guarantee** (§13.3). Full issue drafts:
design doc Appendix A.

**Milestones (project-wide; none existed before 2026-07-11).** Issues are partitioned into six
milestones — the three README goals plus three external-integration increments. The two cross-cutting
epics (#18, the external-integration epic #70) stay unassigned.

| Milestone | Issues |
|---|---|
| Goal 2 · Decomposed NCIt | #4, #9, #57, #60, #61, #62 |
| Ext-Integration · Phase A — Bridge foundation | #71, #72, #73, #74, #76 (✅ #71, #74, #76 closed; #72, #73 reopened) |
| Ext-Integration · Phase B–C — Bind + morphology | #78, #79, #80, #75 (✅ #77, #81 closed) |
| Ext-Integration · Phase D — Serve & caDSR coverage | #82, #83 |
| Goal 3 · Balanced graph | #5 |
| Goal 4 · Post-coordination grammar | #6, #84 |

Full issue drafts + acceptance criteria: external-integration design doc Appendix A.
