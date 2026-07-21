# Decisions

Running log of consequential decisions. Newest first. Each entry: context → decision → why.

## 2026-07-14 — cancer-registry usability: the NCIt ↔ caDSR ↔ NAACCR touchpoint

### D40. NCIt is the reference backbone a FHIR/mCODE-modernized NAACCR binds to *through caDSR* — not a replacement for NAACCR; registry coverage is a caDSR-scoped `COV` number whose critical path is #75
Cancer registries are a critical consumer, so straightforward mappability to NAACCR is a first-class
objective — but "map NCIt to NAACCR" is the wrong frame and risks importing NAACCR's flat legacy.
**Decision:** treat **caDSR as the concrete touchpoint** (caDSR already registers the NAACCR/SEER data
standards and anchors each CDE's semantics + value domain to NCIt), and adopt the posture: NCIt supplies
only NAACCR's *terminology* layer; NAACCR keeps its exchange format, operational rules (reportability,
Solid Tumor Rules, edits), and governance/mandate. Tactics are all measured and additive: (1) the
NAACCR-mappability number is the existing `COV` (§13.3) **scoped to the NAACCR/SEER caDSR-CDE subset** —
a filter, not a new build; (2) registry coverage lives in the **value-meaning** layer, so its critical
path is **#75** (value/qualifier mapping), not the anatomy/cell filler work (#77–#79) — which is why
registry `COV` reads ~0 today; (3) a **tri-partite, owner-attributed gap loop** (NAACCR-no-CDE /
caDSR-annotation-gap / NCIt-cannot-express) keeps NAACCR from "poisoning the well"; (4) map through the
**decomposed `op:` representation**, never the flat legacy code. **Why:** it converts an unfalsifiable
"NCIt could serve registries" into a measured, scoped number, reuses the caDSR machinery already built,
and rides the FHIR/mCODE convergence registries are already adopting instead of forking a parallel
standard. Full strategy, tactics, references, and risks:
[`docs/ecosystem/ncit-cadsr-naaccr.md`](ecosystem/ncit-cadsr-naaccr.md) (first of a downstream-program
series; CTRP/ClinicalTrials.gov, CRDC, and CCDI docs to follow).

## 2026-07-14 — staging editions are not duplicates (a domain error in our own motivating example)

### D39. Never collapse concepts that differ only in staging edition or in a negated finding — they are different assertions, not re-enumerations
`README.md` described "Stage III Thyroid Gland Medullary Carcinoma **AJCC v7**" and its **v8**
counterpart as *"identical clinical entities re-enumerated for a terminology update"*, and #61 carried a
task to *"collapse version/finding siblings into one core entity."* **Both were wrong**, and the error
sat in the project's motivating example.

**The domain fact.** The AJCC 8th edition is not a re-print of the 7th. The 7th staged on anatomy alone
(tumour size and spread); the 8th incorporates tumour biology — HPV status in oropharyngeal cancer,
depth of invasion in oral cancer — and constructs prognostic stage groups from genetic, molecular and
biological factors. The documented consequence is **stage migration**: the same patient is upstaged or
downstaged between editions. "Stage III per v7" and "Stage III per v8" therefore describe **different
populations with different prognoses**.

**Decision.**
1. **Concepts differing only in staging edition are NOT merged, grouped as equivalent, or treated as
   duplicates.** The edition is semantically load-bearing, and it is already modelled correctly: D23
   makes the staging manual a **first-class axis** (`decomposition/axes.py`). Decomposition *factors the
   edition out*; it never collapses across it.
2. **The same holds for `with`/`without <finding>` pairs.** They are distinguished by **negation** —
   presence versus absence of a finding. Merging them would assert that a finding both holds and does
   not.
3. Any future "group the variants" feature is a **presentation** grouping for navigation ("this concept
   has variants across staging editions"), and **may never assert sameness**. #132 is re-scoped
   accordingly.

**Why this matters beyond the wording.** The pre-coordination defect in this example was never
redundancy — it is **fusion**: a real semantic dimension welded into a name, so it cannot be reasoned
over, queried, or versioned independently. The fix is post-coordination (compose disease core + stage +
edition), not de-duplication. Mistaking fusion for redundancy points the whole engine at merging
concepts that disagree, which is the most destructive thing a terminology refactor can do — and it
would have been *invisible*, because merged concepts do not fail a test.

**Provenance:** flagged by the user, 2026-07-14, against the AJCC 7th/8th-edition literature. The engine
was already right (D23); the *narrative* was wrong, which is the harder kind of error to catch — nothing
in CI reads the README.

## 2026-07-14 — the vision of record, and the four ways its naive form is wrong

### D38. ONTOPRISM's end state, stated so that it is achievable and falsifiable
The project's ambition has grown past what the README's four goals describe: decompose NCIt, split the
conflated roles, rebuild NCIt as a specialization of vetted upstream ontologies, compare its concept
landscape against the oncology literature, and end with a *balanced* terminology covering all of
oncology and compatible with the other medical ontologies. That arc is now written into `README.md`
("The Vision"). This entry records the four corrections applied to it, because in each case the
natural phrasing is subtly wrong, and the wrong version is the one that sounds better.

**1. "Zero pre-coordinated concepts" → "no pre-coordinated concept without a sanctioned, reversible,
genuinely atomic definition."** The literal goal contradicts backwards compatibility: an
`owl:equivalentClass` axiom needs a left-hand side, and caDSR's CDEs reference pre-coordinated NCIt
codes, so deleting those concepts breaks the anchoring the caDSR coverage guarantee (§13.3) exists to
protect. It also contradicts the prior art we are otherwise following: GALEN attempted full
elimination and was not adopted; SNOMED CT retains pre-coordination and *sanctions* post-coordination
(MRCM). The achievable goal is zero **unanalyzed** pre-coordination, measured by two metrics that
bracket it — `roundtrip_fidelity` (did we capture everything the source asserts?) and
`residual_precoordination` (is what we produced actually atomic? — see D37).

**2. "NCIt as a subset of Uberon/SNOMED/ICD-O-3" → a dual-canonical mapping layer, split by licence.**
NCIt is not a subset: it holds concepts with no upstream counterpart and its class structure differs.
D24–D26 already say *additive, dual-canonical mapping*, and that is right. Added here is the licence
boundary, which is a hard constraint on what can be *built*, not a legal footnote: **Uberon / CL /
Mondo are open and may be depended on definitionally; SNOMED CT and ICD-O-3 are licence-gated and may
only be mapped to.** An NCIt whose definitions depend on SNOMED cannot be redistributed — which would
defeat the purpose of building it. (#80 stays blocked on a written licence determination, D29.)

**3. "Balanced = equal semantic distance" → balance is a metric to improve, not an invariant to
enforce.** Concept density in a real terminology follows clinical and research need and is *supposed*
to be uneven. Enforcing homogeneity means merging genuinely distinct concepts or minting concepts
nobody needs — destroying information in the name of symmetry. So we **measure and publish the
imbalance and use it to target enrichment where coverage is demonstrably thin.** #5 is reframed
accordingly.

**4. The PubMed comparison finds gaps; it does not measure balance.** Embedding and clustering
abstracts yields a **literature-attention** landscape, and cosine distance in an embedding space is not
semantic distance in an ontology. Publication counts are skewed by funding and fashion, so "NCIt
disagrees with the embedding geometry" is not evidence of an NCIt defect — conflating the two would
manufacture findings. The falsifiable questions are: **which concepts does the literature discuss that
NCIt cannot express, and which NCIt concepts does nobody ever use?**

**4b. The stage-4 guardrail must survive into stage 5 — or it was decoration.** Correction 3 says to
"target enrichment where coverage is thin," and stage 4 is what identifies thin. Read carelessly, those
two compose into exactly the bias stage 4 disowns: *enrichment driven by publication density enriches
where the field publishes*, not where the ontology is genuinely weak — importing funding and fashion
straight into the terminology's shape, one enrichment at a time, while each individual step looks
evidence-driven. So enrichment is targeted on the **falsifiable signal only** (concepts the literature
can express that NCIt cannot), **never on attention or cluster density**. A cluster being large is not
a reason to subdivide a branch.

**5. "Grounded in a vetted substrate" does not mean "losslessly equivalent to it."** The mapping layer
is a **maintenance liability**, and the design already says so (D24–D29, design §14): cross-ontology
maps rot at roughly 6–10% per upstream release (hence the D29 lifecycle and the staleness sweep), and
SKOS `broadMatch`/`narrowMatch` are **not** identity — only a validated `exactMatch` is. As of today
`COV` is still ~0 and mapping precision is gated on SME sign-off of the golden set plus #73's promotion
(which now reaches source-agreement pairs and nothing further). Stage 3 is therefore the claim with the
most distance left to travel, and the vision must not read as though the grounding is already achieved
or is free to maintain.

**Why:** every one of these corrections converts an unfalsifiable or unbuildable claim into a measured
one. That is the same discipline that produced the published `COV` number instead of an
"interoperability for free" assertion, and the same discipline that caught #73 promoting nothing while
reporting success.

## 2026-07-14 — #126: what `residual_precoordination` actually counts

### D37. Residual pre-coordination = a decomposition whose own constituents are not atomic
Design §10 asks for a `residual_precoordination` metric and defines it only as "candidates left with an
unresolved multi-aspect label after roles+NLP" — a description, not an operational rule, which is why
`run.py` has carried a "not implemented yet" note rather than a wrong implementation. Two readings were
on the table.

**Decision: a concept is residually pre-coordinated iff it decomposed (produced at least one
constituent) and at least one emitted constituent is *itself* classified as pre-coordinated by the same
detector.** It is reported as a fraction of decomposed concepts.

**Why this and not the label-coverage reading** ("the constituents do not account for every aspect the
label expresses"):
- The label-coverage question **is already answered, and better**, by `roundtrip_fidelity` (D21.3): the
  fraction of *stated OWL restrictions* covered by the emitted equivalence axiom. That is the same
  question asked of the source's own axioms rather than of NLP-extracted "aspects".
- A metric built on NLP aspect extraction moves when the NLP model changes. It would measure our NLP,
  not our ontology — and a quality metric that drifts with an unrelated component is worse than none.
- The two metrics then bracket the goal cleanly and independently: `roundtrip_fidelity` asks **"did we
  capture everything?"** (completeness); `residual_precoordination` asks **"is what we produced
  actually atomic?"** (irreducibility). Nothing else measures the second, and per D38 it is the
  measure of the project's core claim.
- It is computable with machinery that already exists (`decomposition/detector.py`), and it is
  reachable — a metric that can only ever read 0 is not a metric, and must be proved non-zero on input
  that should trigger it.

**The limit of this metric, stated plainly (do not let it be forgotten).** `residual_precoordination`
is **detector-relative**: it measures *reducibility as seen by our detector*, not ground-truth
atomicity. It is a fixed point of `detector.py`. Two consequences follow, and both must be reported
alongside the number:
1. If the detector **under-detects**, the metric reads artificially low — the ontology looks more atomic
   than it is, which is the direction of error that flatters us.
2. A **detector improvement moves the metric with no ontology change at all.** That is a milder form of
   the very objection used above to reject the label-coverage reading, and honesty requires naming it
   rather than pretending the asymmetry away. It is milder for two reasons — the detector is applied
   consistently everywhere (so the metric stays internally comparable), and the detector is *our own
   deliberate model of pre-coordination* rather than a third-party NLP artifact whose behaviour we do
   not control. But it is real.

**Therefore the metric must be pinned against the curated golden set (#57), not only reported.** Track
`residual_precoordination` on the SME-validated concepts as well as on the corpus: when the two diverge,
the detector has drifted, and the corpus number silently changed meaning. A detector-relative metric
without a ground-truth anchor is a number that can improve while the ontology gets worse.

## 2026-07-14 — #122: where per-promotion evidence lives

### D36. Persist promotion evidence as a `jsonb` column on `concept_xref`
A promoted bridge records *that* it was promoted and never *why*: `PromotionReport.as_dict()` lands
aggregate counts in `xref_run.metrics`, while the per-candidate `Evidence` tuples that actually drove
the decision are computed in `validate_candidate` and discarded. An SME reviewing a bridge, and anyone
asking "why did this pair stop promoting after the release?", needs the second.

**Decision: add an `evidence jsonb` column to `concept_xref`** (a new migration in the raw-SQL style of
`0004_xref.py`), carrying the `Evidence` tuples (kind, source, detail) behind each promotion.

**Rejected — repurpose `mapping_justification`.** It is the *generating* signal, and it is load-bearing
for `evidence._GENERATING_SIGNALS` (the D28/D34 non-circularity rule): overloading it would couple two
unrelated things and break the composite-candidate rule.

**Rejected — a separate `xref_evidence` table.** Evidence is 1:1 with the bridge and is never queried
independently, so normalization buys a join on every read and nothing else.

**Implementation note (not optional):** asyncpg does **not** adapt a bare dict to `jsonb`. The working
pattern is already in-tree — `XrefStore.update_run_metrics` does `json.dumps(...)` plus
`CAST(:x AS jsonb)`. A mocked test will happily accept a dict and pass; only a real-Postgres test
catches it.

## 2026-07-13 — PR/D35: issue-close policy

### D35. PR bodies must only reference issues they resolve; issues must be scoped to a single PR unless they are epics

PR #117's body contained `Closes #73`, but #73 required a follow-up to make
structural corroboration an effective second signal — so the issue auto-closed
prematurely and had to be reopened. Mechanism: GitHub keyword-based auto-close
(`Closes`, `Fixes`) fires on merge regardless of tracking scope, while the
sidebar-linked setting (D35's companion toggle) is already enabled.

**Policy:**
1. PR bodies may use `Closes #X` / `Fixes #X` only when the PR *fully resolves*
   the referenced issue.
2. Every issue (except those labeled `epic`) must be scoped to fit in a single PR.
   Epics track multi-PR bodies of work and are never referenced in a `Closes` keyword.

## 2026-07-13 — #73: implementing D33 Option 1 (what it actually took)

### D34. Two passes that independently produce the same pair yield ONE composite candidate, and that candidate's evidence drops nothing
Implementing D33 Option 1 surfaced two facts that make the decision as written a **no-op**, and this
entry records what the option actually requires. Both were invisible to the (green, strictly-TDD'd)
hermetic suite, and both were found only by interrogating the live stores — the failure mode AGENTS.md
§Testing describes.

**1. The xref pass never matched anything on real data.** Uberon/CL write their NCIt cross-references as
`oboInOwl:hasDbXref "NCIT:C12468"`. Ingest and promotion filtered on the prefix `"NCI:"`, and
`STRSTARTS("NCIT:C12468", "NCI:")` is **false** — so on the live store *zero* of the 2,542 UBERON/CL
classes carrying an `NCIT:` xref were seen: no xref candidates, and `XREF_ASSERTION` evidence that could
never fire for any candidate anywhere. This, not the ingest partition alone, is the mechanical reason
#73 "promoted only curated pairs". Every fixture in the suite spelled the prefix exactly as wrongly as
the code did, so the tests agreed with the bug. It is now pinned by a data-shape contract
(`test_upstream_data_contract`) that reads the real store. Fixed prefix: `NCIT:`.

**2. Emitting two records for one pair loses the agreement at the database.** `concept_xref` is keyed
on `(run_id, subject_id, predicate_id, object_id)` and both candidates are `closeMatch`, so the xref row
and the lexical row for the same pair collide on the primary key (`ON CONFLICT … DO NOTHING`) and the
second is discarded. Dropping the `fillers - matched_via_xref` exclusion therefore changes nothing
downstream by itself: the surviving row is still single-source, `gather_evidence` still drops its one
generating signal, and the pair still has one evidence kind where `is_independent` needs two.

**Decision.** Ingest runs both passes over all fillers (D33 Option 1) and, where they converge on the
same pair, records **one** candidate justified `semapv:CompositeMatching` (a published semapv term: "a
matching process based on multiple matching processes"), confidence 0.95. `evidence.py` maps a
justification to the **set** of signals that generated the candidate and drops that set; for a composite
candidate the set is **empty**.

**Why that is not a hole in D28.** D28's rule is "the signal that generated a candidate may not be
recycled as the evidence that promotes it", written when exactly one signal could generate one
candidate. A composite pair was produced by two independent processes: the label match corroborates the
xref-derived candidate, and the upstream's xref corroborates the lexically-derived one. Neither is its
own evidence. (Formally the evidence for a pair is the union, over its candidate records, of each
record's evidence-minus-its-origin — and that union drops precisely the *intersection* of the origins,
which is empty when the two passes differ. The one record is a storage detail, not a semantic one.)
Dropping both origins would instead make the strongest candidates — an independent OBO curator asserted
the cross-reference **and** the names agree — the only ones that could never promote.

**What does not change.** The bar stays two distinct kinds (or SME curation). A single-source candidate
still drops its origin and still cannot promote on one signal, even with structural corroboration. The
justification is never taken on trust: every signal is re-derived from the store, so a composite row
whose labels have since diverged gathers one kind and stops promoting. The EL/ELK refutation gate and
the D29 lifecycle are untouched, and the PR #117 can't-lie reporting split
(`promoted_on_curation_alone` / `_with_structural_corroboration` / `_on_source_agreement`) is what makes
the new promotion mix legible — `promoted_on_source_agreement` was unreachable before this and is the
bucket Option 1 opens.

**Effect on real data:** 157 of 172 site/cell-origin fillers now have an xref candidate (was 0), and 115
pairs carry both signals — 115 candidates eligible for source-agreement promotion where there were none.
Option 2 (#78 structural corroboration as an effective second signal) is unchanged and still follows.

## 2026-07-13 — #73: promotion evidence policy (unblock auto-promotion)

### D33. Auto-promotion requires two independent signals; reach it first by co-generating xref + lexical candidates (Option 1), then by strengthening structural corroboration (Option 2); curated-only is the honest interim, not the goal
`#73`/PR #117 shipped a correct-but-inert promotion gate: on real data it promotes **only SME-curated
pairs** (`promoted ≡ |curated pairs|`); ELK, anchors, and disjointness contribute zero. Root cause is
not the gate but candidate *generation*: `candidate_ingest.py` partitions fillers
(`remaining = fillers - matched_via_xref`) and runs the lexical pass only over `remaining`, so a filler
is ever recorded as **either** an xref candidate **or** a lexical candidate — never both. A candidate
therefore cannot accumulate two independent signals (an xref candidate can't use its own xref as
corroboration per D28 non-circularity; a lexical candidate can't have an xref by construction), so the
two-signal bar is unreachable for everything except human-signed pairs.

**Decision (precision-vs-recall + effort trade-off, resolved):**
1. **Option 1 — do now (recommended first).** Drop the `fillers - matched_via_xref` exclusion so both
   passes run over all fillers and one filler can hold **both** an xref candidate and a lexical
   candidate. "OBO xref agrees **and** labels agree" then becomes a reachable two-signal promotion —
   the documented intent. Small, low-blast-radius ingest change; auto-promotes exactly the
   high-confidence set (an independent OBO curator asserted the cross-reference *and* the names match).
   Caveat: xref- and label-agreement are *mostly* (not perfectly) independent — acceptable, and the
   standard SSSOM/UMLS "independent-sources-agree" logic.
2. **Option 2 — do next.** Make #78's `part_of` structural corroboration an *effective* second signal
   (it "barely fires" on cold data today). This is the more principled, genuinely-independent signal
   (graph structure, not strings) and extends promotion to cases Option 1 cannot reach — higher effort,
   lower yield, so it follows Option 1 rather than gating it.
3. **Option 3 — the honest interim, not a chosen alternative.** Until 1/2 land, #73 *is* "a curated-set
   importer with a validation gate that only rejects"; `COV` stays ~0 and must be reported as such.
   Choosing 3 *alone* defeats the caDSR-coverage guarantee, so it is the accurate description of the
   in-between state, not the destination. (Second lever: the golden set is `status: seed`, not
   `sme-signed`, so even the curated path is gated off without `--trust-unsigned-golden` or SME sign-off.)

**Guardrail (unchanged, D28):** the two signals must be genuinely independent; a mapping is never its
own evidence. Keep the can't-lie reporting from PR #117 (`promoted_on_curation_alone` /
`_with_structural_corroboration` / `_on_source_agreement`) so the promotion mix stays legible.

**Why:** Option 1 is cheap, low-risk, matches intent, and moves `COV` off zero for the obvious wins;
Option 2 is the correct depth investment for the harder cases; Option 3 names the interim honestly.
Sequencing 1 → 2 (with 3 as the truthful default in between) maximizes near-term coverage without
weakening the independence guarantee. Full rationale + code map: the reserved-work handover (§2·B, §3).

## 2026-07-13 — #78: structural corroboration walks part_of (D16/D20 revisit)

### D32. Cross-ontology structural corroboration walks `subClassOf` ∪ `part_of`, as stated graph edges, not through ELK
`#73`/PR #117 shipped a corroboration walk that followed `rdfs:subClassOf` only. Verified against
the **live Uberon store**, that made the reasoner-backed structural signal near-dead for the main
use case: Uberon relates an organ to its system with **`part_of`** (BFO:0000050), not `subClassOf`.
Concretely `ASK { UBERON:0002048 rdfs:subClassOf* UBERON:0001004 }` (lung ⊑* respiratory system) is
**false**; the containment is `lung ⊑* respiration organ` (subClassOf) **then** `respiration organ
part_of respiratory system` (part_of) — **neither leg reaches the system alone**, and lung's *own*
part_of chain (`pair of lungs → lower respiratory tract`) dead-ends before the system. So on real
data `structural_corroboration` fired for almost no anatomy pair, and `promoted ≡ |curated pairs|`.
This is #78 (originally a D16/D20 region-vs-organ tie-break spike), reclassified onto the `COV`
critical path.

**Decision:** `promotion.corroboration` now reaches the anchored upstream image via a mixed
`subClassOf` ∪ `part_of` graph walk. `part_of` edges are fetched by `build_upstream_partof_query`
(BFO:0000050 existential restrictions on the object's — and each anchor's — `subClassOf*` ancestor
cone, both ends filtered to expandable prefixes) into `PromotionContext.upstream_partof_edges`, and
handed **as stated graph edges straight to the walk — not through ELK.**

**Exact reach, stated honestly.** The walk itself is a plain transitive closure over the edges it is
handed, but the *query* gathers part_of restrictions only **one hop off the `subClassOf*` cone** — it
does not re-seed from a part_of parent. So the **deployed** reach is `subClassOf*` and
`subClassOf* ∘ part_of` (a *single* part_of hop, the sound `subClassOf ∘ part_of ⊑ part_of`
composition), **not** transitive `part_of ∘ part_of` off the cone. That single hop is exactly what the
canonical organ→system case needs (`lung ⊑* respiration organ` then `respiration organ part_of
respiratory system`, the system being the anchor). Deeper `part_of` chains whose intermediate is
neither an object nor an anchor are not gathered — deliberately conservative: the failure mode is
*under*-reach (a missed corroboration), never a false one. Widening the query to full transitive
`part_of` is deferred until a real case needs it.

**Why not through the reasoner.** `robot reason` classifies over named `subClassOf`/`equivalentClass`
and does **not** echo existential-restriction subsumptions (`∃part_of.X`) back as named edges, so
emitting `part_of` restrictions into the merge would not surface in `inferred`. As the module and
design §4.4.1 already state, ELK's *positive* entailments over this fragment reduce to the transitive
closure a graph walk computes; corroboration was therefore always a graph walk, and widening its edge
set to `part_of` keeps that honest. ELK's distinct contribution stays the **refutation** (disjointness)
gate, unchanged.

**Scope / non-claims.** `part_of` corroboration is **one** signal and still requires a second
independent one to promote (D28 unchanged); it is **not** an equivalence arbiter (OAEI large-bio
shows partonomy alignment still yields false positives — D16's caution stands). It does not
materialize D21 defined-class subsumption. Guarded by: mixed-walk + gate-liveness unit tests
(`test_promotion.py`), a query-shape unit test, a `load_promotion_context` routing test, and
**live-store data-shape contracts** (`test_upstream_data_contract.py`, local integration gate) that
pin the exact facts above so a future Uberon restructure fails loudly and names the assumption.

## 2026-07-12 — repository hardening for a public, bad-actor-resistant posture

### D31. Repository made public; free security scanning + committed security workflows enabled
Follows D30. A full secret-history audit (`gh secret list`, gitleaks over all 120 commits + all
tags, a supplementary regex sweep, and `.env`/key-file checks) found **no secrets** in the repo,
GitHub Actions secrets, or history — so the repo was flipped to **public**.

**Decision:** on going public we enabled the features that are free for public repos —
**secret scanning + push protection**, **private vulnerability reporting**, and **fork-PR
workflow approval for all outside contributors** — and added committed security workflows:
**CodeQL** (default setup, python + js/ts + actions; Copilot Autofix on), **dependency-review**
(blocks high-severity/disallowed-license deps on PRs), and **OpenSSF Scorecard** (weekly + on
push; SARIF → code scanning). Supply-chain hardening: all GitHub Actions are **SHA-pinned**,
Docker base images are **digest-pinned**, Dependabot covers **github-actions + npm + docker**
with a 7-day **cooldown**, and a **zizmor** pre-commit hook catches workflow-security regressions
(unpinned actions, excessive token perms, credential persistence) locally before CI.

**Why:** these close the D30 "deferred to the public flip" list and make the workflow-level
Scorecard checks enforceable locally. The two secret-scanning sub-features (non-provider patterns,
validity checks) require paid GitHub Secret Protection and are unavailable on a personal free
account; three CodeQL `py/path-injection` alerts were verified false positives (guarded by
`_resolve_allowed`'s allowlist + API-key auth) and dismissed with justification. Full require-PR/CI
enforcement on `main` remains gated on a release-bot credential (D30).

### D30. `main` integrity is enforced by a ruleset; require-PR/CI is documented but gated on a bot credential
After the release-pipeline fix (#92) nothing *structurally* protected `main`. We hardened
the repository's GitHub settings toward a safe public posture.

**Decision:** a branch ruleset on `main` blocks **deletion** and **non-fast-forward
(force) pushes**; Dependabot **vulnerability alerts** + **automated security fixes** are
enabled; the default workflow `GITHUB_TOKEN` is read-only and Actions cannot approve PRs
(already in place); merges remain squash-only with branch auto-delete. A `SECURITY.md`
policy and `.github/dependabot.yml` (github-actions + npm version PRs) are tracked.

**Why not also "require a PR + passing CI" on `main` yet:** the release automation
(`release.yml` version commit/tag) and the README-stats bot (`update-readme-code-stats.yml`)
push to `main` with the default `GITHUB_TOKEN`. On a **user-owned** repo the `github-actions`
app cannot be added as a ruleset bypass actor, and a `GITHUB_TOKEN` push carries no
bypassable role — so a require-PR/require-checks rule would block those pushes and re-break
releases (exactly what #92 fixed). Enforcing it therefore requires either (a) a dedicated
release-bot **GitHub App / PAT** added as a bypass actor, or (b) moving the repo under an
organization. Deletion + force-push protection needs neither and is safe because the bots
fast-forward-append (never force-push or delete).

**Deferred to the public flip (free on public repos; unavailable/paid while private):**
secret scanning + push protection, private vulnerability reporting, and fork-PR workflow
approval for outside contributors. Flipping visibility to public is itself a deliberate
human action pending a secret-history audit, not automated here.

## 2026-07-11 — corrections from peer-reviewed review + adversarial red-team (D24–D26 hardened)

Design §13/§14 record the full evidence base. A literature pass and an independent adversarial review
found the first cut of D24–D26 over-claimed in three load-bearing ways; D27–D29 correct them. These are
*corrections to*, not reversals of, D24's strategy — the dual-canonical specialization stands; its
guarantees are made honest and measurable.

### D27. The caDSR mapping target is the *enumerated caDSR anchor set*, not the role-target atoms — and the guarantee is a *published coverage number*, not "for free"
The first draft claimed caDSR CDEs reach upstream "transitively, by construction" because NCIt is
unmutated. **False** (red-team C1, verified against the caDSR read model). caDSR anchors NCIt at
surfaces largely disjoint from the ~20K role-target fillers: `ConceptLink` on object-class/property/DEC
concepts (the role-*bearing*, often pre-coordinated concept) and — critically — `PermissibleValue.
meaning_code` value-domain concepts (*Grade 1/2/3*, laterality, *Positive/Negative*, units), which the
assessment §3.4 confirms are **not** modelled as role fillers. caDSR is also NCI-wide, so many CDEs
anchor outside the neoplasm scope gate. Components can be post-coordinated (a *list* of codes), so
coverage holds only if **every** code is mapped.

**Decision:** the mapping target is `M = C_roles ∪ C_cadsr`, where `C_cadsr` is enumerated from the
caDSR read model across **all** `concept_type`s and **all** `permissible_value.meaning_code`s of in-scope
CDEs (design §13.1). The "map to caDSR" requirement is discharged by a **published CDE-level coverage
report** (§13.3): fraction of in-scope CDEs whose every live anchor carries an identity-grade upstream
link, broken out by component type, anchor-liveness, and predicate strength — with an agreed target, not
a claim of totality. Value/qualifier concepts in `C_cadsr \ C_roles` get their own workstream (no §5
axis covers grade/laterality). This turns an unfalsifiable assertion into an auditable number and is the
systematic mechanism the requirement demands. Evidence: ISO/IEC 11179; Covitz 2003; Nadkarni & Brandt
2006; Jiang 2011/2012.

**Field-level reconciliation (2026-07-11, verified against the code):** caDSR is a read-only **SQLite** repository, not Postgres.
The enumerable NCIt code is `cde_concepts.concept_code`; `concept_type`'s real vocabulary is
`{object_class, property, representation, value_meaning}` (the DEC is a derived grouping in a separate
`cde_decs` table). Value meanings are already first-class rows (`concept_type='value_meaning'`), so
`C_cadsr` enumerates from the single `cde_concepts` table; `permissible_value.meaning_code` (in `cde_json`)
is a cross-check, not the primary surface. Whole-DB denominators: 79,827 CDEs / 996,162 links /
**64,001 distinct concept codes** — empirically confirming `C_cadsr` ⊄ `C_roles`. The decision (target set
+ published coverage number) is unchanged; only the field-level mechanics are corrected.

### D28. Mapping validation must be non-circular, SSSOM-recorded, EL-profiled, and backed by committed reasoner infrastructure (or explicitly downgraded)
D25 said "DL oracle confirms exactMatch." Under-specified in two dangerous ways (red-team C2, H1;
lit F12/F13). SKOS mapping properties are **annotation properties with no logical semantics** — feeding
them to a reasoner as `owl:equivalentClass` imports every mapping error as an axiom; *not* feeding them
leaves the planes logically disconnected so no round-trip is provable. And EL reasoners scale (ELK) but
NCIt+upstream merges can leave EL, where classification over a 10M+-triple graph is intractable.

**Decision:**
1. **Non-circularity is an invariant:** the evidence for an `owl:equivalentClass` bridge may never be the
   mapping itself; it requires independent signals (label/definition + structural corroboration or human
   curation). The logical bridge is a **separate curated axiom**, held apart from the `skos:*Match`
   annotation.
2. **Every mapping is an SSSOM record** (predicate, justification, confidence, both endpoint versions) —
   Matentzoglu et al. 2022. `skos:exactMatch` is never derived from a shared UMLS CUI alone (CUI =
   editorial synonymy). Volume of xrefs is not evidence of correctness.
3. **Validation reasoner is profiled to OWL 2 EL**, satisfiability-checked before classification, over the
   stated `owl:equivalentClass`/`intersectionOf` structure — never `rdfs:subClassOf+`, never the inferred
   graph (D21). Triple count is not the cost driver; expressivity is.
4. **Infrastructure is named or the criteria are downgraded:** #NEW-3 must commit tool/profile/runtime/
   owner for the classification job, *or* the round-trip criteria (§12.5) fall back to D21's materialized-
   definition structural check. Shipping a "reasoner-validated" number without committed infrastructure is
   forbidden. Imports discipline: MIREOT partial imports (Courtot 2011), not full-OWL upstream imports.

**Committed reasoner (2026-07-11): ELK, driven via ROBOT — free, local, no cloud.**
- **ELK** (consequence-based OWL 2 EL reasoner; **Apache-2.0**, free) is the classifier. It classifies
  SNOMED CT (~300K classes) in seconds on a laptop and is the reasoner the OBO ontologies we integrate
  (Uberon/CL/Mondo) are themselves built and released with, so profile compatibility is a solved problem
  on the upstream side. NCIt (~200K classes), profiled to EL per point 3, is comfortably within budget.
- **ROBOT** (BMC Bioinformatics 2019; OBO-community-standard CLI wrapping the OWL API + ELK; free) is the
  driver: `robot reason --reasoner ELK`, plus `relax`/`reduce`/`merge` and consistency checks. The
  validation harness (#NEW-3) shells out to ROBOT from the Python data-build; `owlready2` is an optional
  Python-native path for small ad-hoc checks only (it bundles HermiT/Pellet, which do **not** scale to
  NCIt size — not for full classification).
- **Fallback for any subset that escapes EL:** **Konclude** (parallel tableau OWL 2 DL reasoner;
  **LGPLv3**, free) for full-DL classification of a bounded fragment. HermiT/Pellet/Openllet remain
  free options but do not scale to the full NCIt class count.
- **Runtime/host:** local Apple Silicon M4 Max, 128 GB — massively over-provisioned (ELK needs single-digit
  GB and seconds–minutes for this workload). Give the JVM a generous heap (e.g. `-Xmx32g`). **No AWS
  sandbox required**; reserve cloud only if a future full-DL Konclude run on a pathological fragment ever
  needs it (not anticipated).
- **Cost: $0.** Entire reasoning stack (ELK + ROBOT + Konclude) is free/open-source. Commercial engines
  (RDFox, Stardog, GraphDB EE) are **not** needed: they do Datalog/OWL 2 RL *materialization*, not the EL
  *classification* D28 requires — a different tool for a different job.
- **Owner:** the mapping-validation harness (#NEW-3), invoked in the `data-build`/`map` pipeline.

### D29. Mappings have a lifecycle and rot on release; the "identifiers-only" license safety is confirmed-then-served, not assumed; economics are curation-grade
Three governance corrections (red-team H2/H3/H4/M3; lit F8/F9/F11/F14).
1. **Lifecycle + drift.** A mapping is `proposed → validated → {active | quarantined | retired}`. An
   endpoint version bump **re-runs validation** over the affected set (computable from SSSOM version
   fields) and quarantines stale mappings — it does not merely "fail loudly." `$translate` never serves
   non-`active` mappings, and translating an upstream expression into the NCIt plane must return the
   **legacy anchor** where one exists (prevents dual-identity re-duplication). Expect ~6–10% error
   re-injected per upstream release (Groß 2016; Dos Reis) — a **standing maintenance LOE**, separate from
   the decomposition ~5–8 pm, not folded into it.
2. **Economics honesty.** "Mappings largely already exist" is qualified: candidate xrefs exist in volume,
   but oncology NCIt↔ICD-O-3/ICD-10 maps are missing/inconsistent (PMC5294908) and inter-terminology
   precision is often low. Upgrading candidates to inference-grade `owl:equivalentClass` is curation-grade
   authoring; the golden-mapping-set construction is a costed workstream (#NEW-13).
3. **Licensing is served-gated and legally confirmed.** SNOMED CT is affiliate-licensed (UMLS Appendix 2);
   ICD-O-3 is WHO-copyrighted content. A public `$translate` emitting SCTIDs/ICD-O-3 codes may itself
   require affiliate/WHO compliance — the identifier-in-a-map can be the licensed artifact. Obtain a
   **written license determination**, gate the **serving** surface by consumer entitlement (not just the
   build flag), and rely on open Uberon/CL/Mondo (CC-BY/CC0) for a complete default product.

## 2026-07-11 — strategy shift: NCIt as a specialization of the OBO/SNOMED substrate (dual-canonical, additive)

Full design-of-record: [`docs/design/ncit-external-integration.md`](design/ncit-external-integration.md).
Origin: external feedback (a local input memo) recommending an OBO Foundry + SNOMED/ICD-O-3 +
Mondo composite architecture for a next-generation NCIt.

> **Note (2026-07-11, post-review):** D24–D26 below are *hardened by D27–D29 above* following a
> peer-reviewed literature pass and an adversarial red-team. Read them together: the strategy is
> unchanged; the caDSR guarantee is now enumerate-then-measure (D27), validation is non-circular and
> reasoner-committed (D28), and mapping lifecycle/economics/licensing are corrected (D29).

### D24. Adopt "NCIt as an oncology-specific specialization of a vetted upstream substrate," realized as a dual-canonical, additive bridge — not a re-platforming
The feedback's correct intent (be compliant with, and build on, the vetted upstream ontologies —
Uberon anatomy, Cell Ontology normal cells, SNOMED CT + ICD-O-3 morphology, Mondo/DO disease) is
adopted. Its literal prescription — *extract NCIt's anatomy/cell axes and replace them with upstream
IRIs* — is **rejected** because it violates the project's load-bearing invariant (additive, never
mutate the stated OWL; D4/D19) and would break both backward compatibility and caDSR CDE anchoring.

**Decision:** NCIt keeps what is genuinely its own — oncology-specific pre-coordinated combinations,
AJCC staging, chemotherapy regimens, NCI-curated oncology vocabulary — and *defers* the general
anatomy/cell/disease/morphology scaffolding to the upstream reference ontologies **by mapping to
them, not absorbing or replacing them**. Realized as a **dual-canonical** model (user-chosen posture):
- **Reference plane = NCIt (canonical-of-record for everything that exists today)** — un-mutated,
  backward-compatible, the anchor caDSR CDEs point at.
- **Canonical plane = the upstream stack (canonical for *new* post-coordinated authoring + interop)**.
- **Join = an additive mapping layer** (`skos:*Match` + RO `has_location`/`derives_from`/
  `has_material_basis_in` + FHIR `ConceptMap.$translate`), always present, both directions.

The oncology concept is then an OBO-style **cross-product** (lit review §4.2, GO cross-products [26]):
a decomposed NCIt neoplasm's `op:` axes point at upstream fillers over a Mondo disease genus — NCIt
supplying the specialization, the substrate supplying the reusable parts.

**Why this is the right synthesis and not a course reversal:** the `op:` univocal axes (D17/D20/D22/D23)
*are already* the Relation-Ontology relations the feedback asks for; SNOMED relationship groups (D19),
the SCG/ECL/MRCM grammar (D22), and FHIR `$translate` (D22) are already adopted. And the decisive
empirical finding — decomposition surfaces ~20K role-target atoms, **100% already existing active
concepts** (assessment §3.2) — means the atoms need not be imported; only *mapped*. Mirroring that,
the mappings themselves largely already exist (NCIm UMLS CUIs for SNOMED/ICD-O-3; Mondo's own NCIt
xrefs; Uberon/CL xrefs), so this is an ingest/validate/serve exercise, not a rewrite. Nothing that
exists today breaks; caDSR is never touched and gains upstream reach transitively (D26).

### D25. The mapping layer uses honest SKOS relations, versioned provenance, and a DL-classification oracle — the D21 rule extended across ontologies; Uberon is revisited as an xref/interop target (not a tie-break default, so D16 stands)
Cross-ontology maps are curated assertions, not scrapes. **Decision:**
1. **Honest relations, never a flat `sameAs`.** Record `skos:exactMatch`/`closeMatch`/`broadMatch`/
   `narrowMatch`/`relatedMatch` per the true granularity; use RO object properties (`has_location`,
   `derives_from`, `has_material_basis_in`) for typed non-identity bridges. UMLS co-occurrence ≠
   equivalence.
2. **Map-before-mint.** Author a mapping only where no vetted source (NCIm/Mondo/Uberon/CL xref)
   supplies it; authored mappings enter the D23 review/provenance workflow (`concept_xref`/`xref_run`
   tables, review status, confidence, UMLS CUI, run/version pins).
3. **DL oracle, extended from D21.** A proposed `exactMatch` is promoted only if a real OWL reasoner
   over the stated `owl:equivalentClass`/`owl:intersectionOf` structure confirms mutual subsumption;
   otherwise it is demoted. **Never `rdfs:subClassOf+`, never the inferred graph** (D21; Bodenreider
   et al. divergence [12]). This gates every `exactMatch` and the `--emit-equivalence` cross-product.
4. **Uberon revisit — complementary to D16, not a reversal.** D16 declined Uberon *as the default fix
   for R101 most-specific-filler ties* (only 1 of 4 residual ties looked like a Uberon win); that
   finding stands. Uberon re-enters in a *different* role — equivalence mapping + interoperability
   substrate (already loaded at :7879) — plus a **scoped** re-test of Uberon `part_of` against exactly
   the region-vs-organ ties D20 routes via filler-semantic-type (Uberon containment is richer than
   NCIt's sparse `R82`). If the re-test fails, D16/D20 stand; nothing regresses.

### D26. Licensing is a first-class build gate; open ontologies carry the default experience, licensed sources are flag-gated and store only NCIt→code maps
Uberon/CL/Mondo are open (CC-BY/CC0) and unconstrained. SNOMED CT requires a member/affiliate license;
ICD-O-3 is WHO-licensed. **Decision:** SNOMED/ICD-O-3 mappings live behind a build flag, off by
default; the platform stores and serves only **NCIt→upstream identifiers and relations** (the
NCIm/UMLS-license-compatible surface), never bulk upstream content, and does not redistribute the
licensed ontologies. Upstream releases are **version-pinned** in parallel to the NCIt build pin (D5);
a bump fails loudly and re-runs mapping validation, because cross-ontology maps rot when either
endpoint releases. The open-licensed anatomy/cell/disease mappings (Uberon/CL/Mondo) provide a
complete default product without any licensed dependency.

## 2026-07-11 — SME review: organ-level R101 principle, op: namespace approval, and minted concepts

### D23. R101 resolution = the named organ (SME-approved principle); `op:StageSystem`, `op:MolecularAbnormality`, `op:MetastaticSite` are first-class axes; minted concepts for missing NCIt terms are tracked in git
SME review of the draft golden set (30 neoplasm concepts) produced a single governing rule that supersedes the per-cancer tie-resolution table in prior drafts, ratified the `op:` namespace for decomposition axes, identified two structural bugs, and required minted concepts for NCIt gaps. Recording all SME decisions as load-bearing.

**The organ-level principle (from C134930 note):**
> "If the emphasis is on the primary site of the tumor then **the organ is typically correct** — the tumor extent which might involve additional structures is a separate concern and **should not be conflated** — this concern is captured in the stage definition of the staging system!"

**R101 = the named organ.** Not the super-system above it. Not the subsite/lobe below it. Extent/spread belongs to **stage**; metastasis belongs to a **separate site axis**.

**Part 1: SME-validated organ-code lookup**

| Morphology Context | Organ Code | Label | Notes |
|--------------------|------------|-------|-------|
| Thyroid Carcinoma | C12400 | Thyroid Gland | Prior draft used C75102 (incorrect) |
| Gastric (non-EGJ) | C12391 | Stomach | NOT C13307 "Gastric" |
| Gastric (EGJ) | C32668 | Gastroesophageal Junction | |
| Small Intestine | C12386 | Small Intestine | Organ level, not subsite |
| Colorectal | C19184 | Colon, Rectum | Composite staging organ |
| Cervical | C12311 | Cervix Uteri | Corpus extension disregarded |
| Lung | C12468 | Lung | |
| Breast | C12971 | Breast | |
| Gallbladder | C12377 | Gallbladder | |
| Pancreas | C12393 | Pancreas | |
| Urethra | C12417 | Urethra | |
| Hypopharynx | C12246 | Hypopharynx | |
| Esophagus+GEJ | C203674 | Esophagus and Gastroesophageal Junction | Composite |

**Part 2: `op:` namespace approved**

All proposal axes from D23 draft are ratified:
- `op:StageSystem` — **approved** (29/30 concepts yes; 1 data fix)
- `op:MolecularAbnormality` (R106) — **must be kept**; PR/ER/HER2 is the textbook case per SME setting change
- `op:MetastaticSite` (R102) — **first-class axis**; distinct from R101 per SME note on brain metastasis of breast tumor
- `op:PrimarySite` (R101) — organ per Part 1
- `op:CellType` (R105) — histology
- `op:AssociatedSite` (R100) — non-primary, non-metastatic
- `op:ClinicalFinding` (R114) — probabilistic, not defining (SME distinction: `Has_*` = defining; `May_Have_*` = optional)
- `op:CellOrigin` (R115) — lineage, optional per above

**Part 3: Settings model change**

| Setting | Old | New | SME Note |
|---------|-----|-----|----------|
| `drop_out_of_scope` | yes | **SPLIT per role** | R106 keep; R114/R115 may drop |
| `include_associated_sites` | yes | **maybe** | Metastatic sites need separate axis |

**Part 4: Collisions = Both**

Same anatomy code legitimately appears on multiple axes (primary + associated). No cross-axis deduplication.

**Part 5: Minted concepts for NCIt gaps**

SME identified that C27787 (testicular NSGCT) has no suitable NCIt cell type. Decision: **mint temporary ID** `MINT-3a7f2c8e901d` for "Malignant Non-Seminomatous Germ Cell" with parent C12917. Minted concepts tracked in `ontolib/tests/decomposition/golden/minted-concepts.json` (git-tracked) for reproducibility.

**Part 6: Structural bugs identified**

1. **C8515** — AJCC v6 concepts missing R88 fillers; walker/q uery gap vs v7/v8
2. **C208097** — SME preferred C19184 (Colon, Rectum) not in walker's R101 candidates; clinical staging convention overrides OWL-stated narrower code

**Why this matters:**
- Replaces per-cancer lookup table with a single principled rule (D22's univocal relation)
- Validates the entire `op:` namespace proposal (D17/D20 era)
- Establishes minted-concept workflow for NCIt gaps (reproducible, git-tracked)
- Flags two bugs blocking golden set completion

**Evidence:** SME review (workbook + decisions log) and `ontolib/tests/decomposition/golden/minted-concepts.json`.

---

## 2026-07-10 — literature grounding: univocal relations, relation-quality-first, and the goal-4 grammar template

### D22. The `op:` axes are univocal relations in the OBO Relation Ontology sense; relation quality gates coverage; goal 4's grammar is SCG/ECL/MRCM + sanctioning — grounded in a peer-reviewed review
A comprehensive literature review of atomic/compositional terminology design was compiled
against 34 peer-reviewed and standards sources
([`docs/postcoordination-literature-review.md`](postcoordination-literature-review.md)).
It confirms the D14–D21 decomposition decisions and adds three points the design had
implicit but never named or cited. Recording them so they are load-bearing, not folklore.

1. **The overloaded-role split (D17/D20) is the OBO Relation Ontology principle, and should
   be named and cited as such.** D17/D20 route `R101`/`R105` senses to distinct `op:` axes
   (`op:AssociatedLineageClassification`, `op:AssociatedRegion`) on empirical grounds. The
   *principled* justification is Smith, Ceusters et al., "Relations in biomedical
   ontologies" (*Genome Biol* 2005;6(5):R46, [doi:10.1186/gb-2005-6-5-r46](https://doi.org/10.1186/gb-2005-6-5-r46)):
   a relation must be **univocal** — one label, one formally-defined sense, with stated
   domain, range, and logical properties. NCIt's `R101` is not one relation but several
   wearing one label; our `op:` axes are the univocal relations that replace it. Each `op:`
   axis we mint **must** carry a stated domain/range/definition, not just a name. NCIt's
   `R82` part-of transitivity gap (D16) and SNOMED's historical SEP-triplet overloading of
   is-a for part-of (Schulz et al. 2009) are the same failure class viewed from other sides.
2. **Relation quality gates decomposition quality — sequence it before coverage.** The
   review's strongest strategic finding: the scarce resource is *univocal relations*, not
   *atoms* (every filler is already an active NCIt concept — 100% roles-path coverage). So
   pushing #44's coverage (currently ~3.24% on the naive baseline) *on top of* overloaded
   roles propagates the `R101`/`R105` conflation into every decomposed concept. The genus-
   sense routing (D17/D20, PR-A) is therefore a **precondition** for coverage expansion, not
   a parallel nicety. This is already the PR order (PR-A before the coverage push); D22 makes
   the *reason* explicit so the order is not reshuffled under schedule pressure.
3. **Goal 4 (#6) has a standards template — use it, don't invent a grammar.** The post-
   coordination expression syntax should be modelled on SNOMED CT's Compositional Grammar
   (SCG) for writing expressions, its Machine-Readable Concept Model (MRCM) for *sanctioning*
   which refinements are valid (the computable descendant of GALEN/GRAIL sanctioning, Rector
   et al. 1997), and its Expression Constraint Language (ECL) for the query layer. This buys
   interoperability and a clean path to HL7 FHIR terminology services
   (`ConceptMap.$translate`) for the pre-↔post equivalence mapping — the same pattern
   ICD-11's sanctioning tables implement. The #6 design, when written, starts here.

**Why this is additive, not a course change:** it renames and grounds decisions already
made and confirms their sequencing; it commits no new engineering beyond "every `op:` axis
needs a stated definition" and "the #6 design starts from SCG/ECL/MRCM." The RO-style global
role-split remains explicitly *not* adopted now (D17's additive genus-sense classification
stands); D22 records it as the eventual *normalization target* once the genus-sense
classification has accumulated enough evidence to define the univocal relations properly.
Full survey, examples, and the mitigation-vs-current-approach comparison table:
[`docs/postcoordination-literature-review.md`](postcoordination-literature-review.md) §6, §8.

## 2026-07-09 — subsumption-closure completeness is a precondition of D19

### D21. NCIt's `rdfs:subClassOf+` closure omits defined-class subsumption, so "nested" is only decidable where it is materialized — accept the fail-safe direction, and do not use the inferred graph as a round-trip oracle
D19's central rule — collapse only *nested* (is-a/part-of) candidates, preserve co-equal
non-nested ones — makes correctness depend on deciding nestedness, which
`filler_selection.py` does via `rdfs:subClassOf+`. That closure is **incomplete**, and more
broadly than §6.4 recorded. Verified against the live store (2026-07-09):

- `C3773 owl:equivalentClass [owl:intersectionOf (C215715 C3809)]` — `C3809` is a named
  intersection member, which *entails* `C3773 ⊑ C3809`.
- `ASK { C3773 rdfs:subClassOf+ C3809 }` returns **false in the stated graph *and* in the
  inferred default graph**, and neither holds a direct `rdfs:subClassOf` edge.

§6.4 attributed this to "the materialized/inferred graph", implying the stated graph (or
another build) might carry it. Neither does. **There is no graph in this deployment against
which defined-class-to-defined-class subsumption can be read off `rdfs:subClassOf+`.**

**Decision:**
1. **Accept the fail-safe direction.** Where a genuine subsumption is not materialized, a
   nested pair is misread as co-equal and therefore **preserved** as separate
   relationship-group members (D19), never collapsed. Nothing is dropped from the lossless
   record of truth; the cost falls entirely on the *curated projection*, which over-reports.
   This is the right way for the error to fall, and it is why residual ties persist after
   D20 rather than being a bug to engineer away.
2. **Precision against a single-valued oracle is capped by this**, not by the boundary
   heuristic. #44's ≥0.9 gate must be measured with `needs_review` excluded (now supported
   by `score.py`) and against a golden set encoding D19/D20's multi-valued axes — otherwise
   the gate is unreachable by construction.
3. **`roundtrip_fidelity` (§10) may not treat the inferred graph as a sound closure
   oracle.** §10 specifies validating the emitted `owl:equivalentClass` unfolding "against
   the **inferred** graph as the closure oracle". That oracle has the *same* blindness, so
   it would report false negatives on exactly the defined-class chains D19 exists to
   preserve. Before `--emit-equivalence` is built out, either compute the closure from the
   stated `owl:equivalentClass`/`owl:intersectionOf` structure (which *is* complete — it is
   the definition) or run a real OWL reasoner. Do not ship a fidelity number derived from
   `rdfs:subClassOf+`.

**Why not just "fix the closure":** the entailment is genuine but unmaterialized; producing
it requires OWL reasoning over the ~10.8M-triple stated build — a separate infrastructure
decision, not a query fix. Recording the constraint costs nothing and prevents a silently
wrong fidelity metric. Evidence: `docs/design/ncit-decomposition-engine.md` §6.4.

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
`docs/design/ncit-decomposition-engine.md` §6.5/§6.6, §4.4, §10.

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
§6.4/§6.6.

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
`docs/design/ncit-decomposition-engine.md` §6.5/§6.6.

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
§6.4; research code was local and untracked.

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
reimplementation following that diagram will reproduce the bug. The investigation used
local, untracked research code.

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
