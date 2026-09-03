# Decisions

Running log of consequential decisions. Newest first. Each entry: context → decision → why.

Decision records use precise ontology terminology. For plain-language definitions of
decomposition, axis, filler, OWL existential restriction, genus, semantic type, curated
projection, source occurrence, partonomy, and relationship group, see the
[shared terminology](../README.md#terminology).

## 2026-09-03 — Python 3.14.7 is the sole runtime

### D84. The pre-production platform is 3.14.7-only and supersedes D83 immediately

**Decision:** Python 3.14.7 is the only supported local, hosted-CI, integration,
data-build, and container runtime; this decision supersedes D83 rather than retaining a
3.13 compatibility architecture (`git diff --no-ext-diff`, 2026-09-03). The root and both
local package manifests require `>=3.14.7,<3.15`, while PDM canonically records the same
lock target as `~=3.14.7` (`pdm lock --python ">=3.14.7,<3.15"`, 2026-09-03).

Every Python setup step uses exact 3.14.7, ordinary test configuration fails
deprecations except for the exact observed third-party Starlette alias warning, and the
former compatibility job, runners, warning module, version module, and special verify
gate are removed (`git diff --no-ext-diff`, 2026-09-03). The backend base is the immutable
multi-platform `python:3.14.7-slim` index digest
`sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6`
(`GET https://hub.docker.com/v2/repositories/library/python/tags/3.14.7-slim`,
2026-09-03).

## 2026-09-03 — Python 3.14 is a required forward-compatibility lane

### D83. Production stays on Python 3.13 while every locked non-integration test and runtime import is certified on 3.14

**Decision:** Python 3.13 remains the production/default, integration, data-build execution,
container, type-checker, and ordinary CI runtime; Python 3.14 is a required clean
forward-compatibility lane for the runtime/application import smoke and complete
non-integration Python suite (`git grep -n python-version -- .github/workflows/ci.yml` and
`git grep -n python:3.13-slim@ -- backend/Dockerfile`, 2026-09-03). The root and both local
package manifests support `>=3.13,<3.15`, and one lock resolves that whole interval
(`git grep -n requires-python -- pyproject.toml ontolib/pyproject.toml
backend/pyproject.toml` and `pdm lock`, 2026-09-03).

Local certification creates a disposable PDM environment, performs a clean `--dev` sync,
then invokes the same two smoke/test scripts as the CI job. The authoritative `verify` gate
includes this local lane; compatibility execution writes no coverage artifact and checks that
the primary environment and existing coverage artifacts remain byte/content-identical
(`pdm run agent-test backend/tests/test_python_compatibility_runner.py -v`, 2026-09-03).
Integration, full-store, full-build/data-build execution, and containers remain 3.13 rather
than duplicating service or corpus certification in the forward-compatibility lane
(`git grep -n python-version -- .github/workflows/ci.yml`, 2026-09-03).

One shared global policy makes every deprecation warning fail both the compatibility smoke and
test lane. Its sole exception is the exact third-party Starlette
`anyio.abc.BlockingPortal` alias message from the exact `starlette.testclient` module currently
observed during test collection; project-owned, generic, and near-match deprecations remain
errors, and pytest-liveness tests exercise both branches (`pdm run agent-test
backend/tests/test_python_compatibility_runner.py -v`, 2026-09-03). The deprecated stdlib
`asyncio.iscoroutinefunction` call used by `retry_with_backoff` was replaced with
`inspect.iscoroutinefunction` without changing sync/async retry behavior
(`pdm run agent-test
ontolib/tests/terminologies/test_retry_backoff.py -v`, 2026-09-03).

## 2026-08-30 — MINT-781c8c8c6096 lifecycle authority is reconciled

### D82. The strict proposal registry is the sole current governance record for the C27787 mint

**Decision:** on 2026-08-30 the user explicitly set the authoritative current state of
`MINT-781c8c8c6096` to `locally-approved`. The strict
`ontolib/tests/decomposition/golden/proposal-registry.json` entry and the augmented C27787
expected constituent in `neoplasm-adjudicated.json` are the current authorities; together they
record exactly one proposal and one constituent with that ID, `op:CellType`,
`locally-approved` provenance, and `needs_review=false`
(`pdm run agent-test ontolib/tests/decomposition/test_m1_baseline.py::test_mint_781_lifecycle_has_one_strict_local_authority -v`,
2026-08-30).

`locally-approved` means local SME approval only. It is not submitted, accepted-in-ncit,
runtime-published, or full-corpus-published. The deterministic ID and all
non-lifecycle proposal fields remain unchanged, as do the augmented constituent, row decisions,
current comparison, engine evidence, and corpus evidence
(`git diff --no-ext-diff -- ontolib/tests/decomposition/golden/proposal-registry.json ontolib/tests/decomposition/golden/neoplasm-adjudicated.json ontolib/tests/decomposition/golden/neoplasm-row-decisions.json ontolib/tests/decomposition/golden/neoplasm-current-comparison.json ontolib/tests/decomposition/golden/neoplasm-engine-evidence.json ontolib/tests/decomposition/golden/neoplasm-current-engine-evidence.json ontolib/tests/decomposition/golden/neoplasm-corpus-comparison.json ontolib/tests/decomposition/golden/neoplasm-current-corpus-baseline.json`,
2026-08-30). This reconciliation changes no runtime graph, store, database, API, frontend, or
publication surface (`git diff --no-ext-diff -- ontolib/src backend/src frontend/src`, 2026-08-30).

The old minted-concept golden JSON was a stale duplicate that still said proposed and unapproved
(`git diff --no-ext-diff -- 'ontolib/tests/decomposition/golden/minted*.json'`, 2026-08-30).
No production source or script consumed it
(`git grep -n "minted[-]concepts[.]json" -- ontolib/src backend/src frontend/src scripts`,
2026-08-30).
It is removed rather than retained as a compatibility record. Runtime database minted concepts and
their API are separate product state and are not changed or certified by this golden-governance
cleanup.

**Deferred blocking technical debt:** the C27787 adjudication rationale still contains historical
phrases including “approved for submission”
(`git grep -n "approved for submission" -- ontolib/tests/decomposition/golden/neoplasm-adjudicated.json`,
2026-08-30). That prose is not current lifecycle authority. Editing it would change oracle identity
and require current-evidence and R103 rebinding. Although `tmp/m1-6-current-replay.ttl` is present
(`ls "tmp/m1-6-current-replay.ttl"`, 2026-08-30), it is not tracked
(`git ls-files "tmp/m1-6-current-replay.ttl"`, 2026-08-30), and no persisted run
ID proving the required rebind was supplied for this batch. Rebinding is therefore **BLOCKED**;
identities must not be hand-edited. No other SME correction is approved by this decision, and the
broader correction feedback remains unresolved.

## 2026-08-29 — group review distinguishes scoreable, review-bearing, and absent pairs

### D81. Historical schema-3 review remains immutable; schema 4 starts a fresh review boundary

**Decision:** current comparison schema 3 classifies every expected/current pair into one of six
disjoint relations: matched scoreable, expected not emitted, expected emitted but not scoreable,
current-only scoreable, current-only review-bearing, or provisional proposed. Axis diagnostics use
a closed current-projection status independently of the range verdict. Active group-review packet
schema 4 carries those pair relations and source-occurrence context while normalized groups include
only release-bound scoreable pairs.

The historical schema-3 packet is admitted byte-identically beside its frozen Markdown and sidecar.
It remains a separate strict model solely to interpret that human record. It is never upgraded,
converted, or fed to schema-4 workbook/import code: doing so would silently assign semantics the
reviewer did not see. The rev2 producer therefore creates a fresh blank schema-4 workbook, a
pair-relation correction audit, and machine validation that the workbook contains no decisions.

**Why:** review-bearing output and release-bound scoring are independent facts. Treating every
emitted pair as scoreable changed group identities and made old rationale appear reusable under a
contract it never reviewed. Preserving the historical bytes while beginning a typed blank boundary
keeps observation separate from inference and prevents accidental human-decision carry-forward.

## 2026-08-28 — the C3264 R103 accuracy exclusion is exact and non-propagating

### D80. A local-SME exclusion suppresses only C3264/R103/C12950 in the curated projection

**Decision:** the terminal #294 revision records the human decision as supplied, retains its
rationale separately from a machine qualification, and applies
`("C3264", "R103"): frozenset({"C12950"})` through the existing concept/role policy. R103 is
non-defining. The exclusion does not transfer to descendants; individual descendants may have
specific embryonic or fetal origins. The stated NCIt assertion, complete definition, source fact,
group, occurrence, and provenance remain evidence rather than being deleted.

This is a local-SME `concept-scoped-accuracy-exclusion`, not NCI acceptance or publication.
Machine readiness may mark only the R103 requirement satisfied; group review, R101 authorization,
and final scientific acceptance/publication remain separate human requirements, so overall
authorization remains false.

## 2026-08-22 — Python domain values and wire documents have separate model systems

### D79. Dataclasses model domain values; Pydantic models validate boundaries

**Decision:** immutable internal facts, evidence, verdicts, and algorithm results use frozen
dataclasses. Strict Pydantic models are reserved for configuration, API DTOs, persistence,
manifests, and serialized CLI/report artifacts. The two representations never contain one another;
an adapter maps their fields explicitly. Stateful readers, repositories, and orchestrators remain
ordinary classes rather than being forced into either value-model system.

**Why:** dataclasses make domain equality and algorithm inputs explicit without serialization or
coercion semantics. Pydantic provides the fail-closed validation and stable wire shape required at
trust boundaries. Mixing them in one object graph makes it unclear whether construction represents
a domain operation or parsing untrusted data, and lets a wire-library behavior become an accidental
semantic invariant.

Applying this rule changed the R101 detector identity to
`7ca7924792a82c1822a278bd817b41392587a30779d7431827b47cb926269f46` because shortest-path
resolution now uses domain dataclasses and converts explicitly at the report boundary. A deterministic
rebind of the tracked D77 payload changed only `detector_identity`, `json_identity`, and
`report_identity`; all 43,414 occurrence rows, grouping rows, counts, query metrics, and the exact TSV
identity remained equal
(`pdm run python -c 'import gzip,json,pathlib,subprocess; old=subprocess.run(["git","show","f17fa44:ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz"],check=True,capture_output=True).stdout; a=json.loads(gzip.decompress(old)); b=json.loads(gzip.decompress(pathlib.Path("ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz").read_bytes())); print({k for k in a.keys()|b.keys() if a.get(k)!=b.get(k)},a["occurrences"]==b["occurrences"],a["counts"]==b["counts"],a["query_metrics"]==b["query_metrics"],a["tsv_identity"]==b["tsv_identity"])'`,
2026-08-22). Full regeneration from the historical database is blocked because its v3 baseline run
stores obsolete fingerprint schema 2 and no `collapse_policy_identity`; current readers correctly
fail closed rather than carrying a legacy compatibility reader.

## 2026-08-20 — R101 coverage review is human-centered and occurrence-bound

### D78. Review non-exclusive projection coverage without asserting disease exclusivity

The generated schema-v3 packet contains 162 endpoint patterns, 2,800 disease propositions, 3,291
exact source-occurrence audit records, and 2,800 frozen membership rows; disease-proposition
multiplicity is 2,322×1, 465×2, and 13×3, with maximum pattern fanout 245
(`pdm run python -c 'import json; from pathlib import Path; from collections import Counter; p=json.loads(Path("tmp/r101-review-packet-v3.json").read_text()); print(len(p["patterns"]),len(p["disease_propositions"]),len(p["occurrences"]),len(p["membership"]),Counter(x["occurrence_count"] for x in p["disease_propositions"]),max(x["occurrence_count"] for x in p["patterns"]))'`,
2026-08-20).

**Decision:** #267 uses one canonical packet, a human-centered workbook, a proposed atomic decision
registry, and a read-only decision-expansion dry run. The atomic audit subject remains disease
concept + exact R101 source occurrence + broader site + retained more-specific site. The primary
human decision is the endpoint pattern; import expands it over immutable packet membership and may
exclude a disease only through an explicit `Yes` plus nonempty rationale. Approval means only
`non-exclusive projection coverage`: the retained site may cover the omitted broader site in the
curated projection for the listed disease/source occurrences. It never means equivalence,
universality, completeness, exclusivity, or the only valid site, and source assertions remain
preserved. Multiple valid narrower sites remain independent.

The workbook has exactly six sheets: `Instructions and Semantics`, `Pattern Review`, `Disease
Propositions`, `Column Definitions`, `Review Examples`, and a veryHidden `Bindings`. It has no
occurrence appendix, technical row IDs, or raw JSON, and reviewer-facing sheets do not require
reviewers to handle hashes. `Bindings` carries the exact SHA-256 packet, guidance, visible-row,
and membership identities plus schema and release for mechanical binding. The only unlocked cells
are 162×4 pattern review cells and 2,800×2 disease exception/rationale cells. Every disease row is
generated with `Exception?=No` and a blank rationale; this is a scope default with no effect until
its pattern is approved. Reviewers change only true exceptions to `Yes` and supply rationale. A
missing or invalid value refuses import; every non-approve pattern must retain `No` and blank
rationale
(`pdm run pytest ontolib/tests/decomposition/test_r101_review.py -q`, 2026-08-20). Hiddenness is not
security: import regenerates every immutable visible cell from the separate packet. Benign XLSX
container re-saves are accepted, while semantic cell edits, stale guidance/bindings, formulas,
missing/duplicate/extra rows, macros, and external links refuse the whole import.

The canonical packet identity is
`fa9cca72f60affedf20ff420423f5f30c1aeabcff1bc54d53b05a6a7b419fc59`; its guidance,
visible-row, and membership identities are respectively
`fc315ee3633585693bd6db22f193c83b23fec94ad35412cafb80d40898b4c39b`,
`97a1d3084e9f555887bf931424c707b33083fbe654b20a1ef417a0462e35f6f7`, and
`756943698475d2313d7c1c6802fb2e0055585f5ce005b1575a48d0f8aa8702dd`
(`pdm run python -c 'import json; from pathlib import Path; p=json.loads(Path("tmp/r101-review-packet-v3.json").read_text()); print({k:p[k] for k in ("packet_identity","guidance_identity","visible_rows_identity","membership_identity")})'`,
2026-08-20). The packet and workbook file SHA-256 values are
`82c865f0b25624c2b6e968b724383385b55748c393650714281c16eceee701dd` and
`8d8993cac4373f67a99022e3db60f917b82f0cb160444e7159d9ca32f8fb4a35`
(`shasum -a 256 tmp/r101-review-packet-v3.json tmp/r101-review-workbook-v3.xlsx`,
2026-08-20).

The workbook records the supplied SEER/ICD-O pilot conclusion only as generic guidance: zero strict
rule-eligible cases means no automation and no safe workload reduction; it exposes no SEER decision
fields (`pdm run python -c 'from openpyxl import load_workbook; b=load_workbook("tmp/r101-review-workbook-v3.xlsx"); print("\\n".join(str(c.value or "") for r in b["Instructions and Semantics"].iter_rows() for c in r)); print(tuple(c.value for c in b["Pattern Review"][1]))'`,
2026-08-20). Exact external pilot command/date/input hashes are not present in the workspace, so
the detailed pilot counts cannot be durably certified here; that documentation remains blocked on
independent evidence rather than being reconstructed from memory.

The TEST-ONLY all-approve/no-exception import expanded to exactly 3,291 proposed atomic decisions
and the dry run reported `writes_performed=false`; the tracked conservation report remained pending
and publication-blocked
(`pdm run adjudication import-r101-review-decisions --packet tmp/r101-review-packet-v3.json --reviewed-xlsx tmp/r101-review-workbook-v3-TEST-ONLY.xlsx --output tmp/r101-review-registry-v3-TEST-ONLY.json --provenance test-only && pdm run adjudication dry-run-r101-decision-expansion --report ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz --packet tmp/r101-review-packet-v3.json --registry tmp/r101-review-registry-v3-TEST-ONLY.json --output tmp/r101-review-preflight-v3-TEST-ONLY.json`,
2026-08-20). Expansion creates a proposed decision registry, never a new packet, authorization, or
publication artifact. This decision does not implement D75/#271.

## 2026-08-19 — R101 conservation is an occurrence ledger, not content approval

### D77. Bind every source occurrence and replayable stated-R82 edge before review

The generated schema-3 artifact partitions 43,414 R101 occurrences into 30,040 projected,
10,083 unchanged-unprojected, 1,954 one-step-R82 covered, 1,337 closure-only-R82 covered, and
zero unresolved rows, with zero non-R101 delta
(`pdm run python -c 'from pathlib import Path; from ontolib.decomposition.r101_conservation import load_r101_conservation_report; r=load_r101_conservation_report(Path("ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz")); print(r.counts.model_dump())'`,
2026-08-19). Every path edge records the traversal endpoints, asserted restriction subject,
restriction node, exact source identity, and recomputed fact identity; the persisted compressed
JSON and on-demand TSV roundtrip contract passes
(`pdm run pytest ontolib/tests/decomposition/test_r101_occurrence_ledger.py::test_full_structural_key_survives_model_json_and_lossless_tsv ontolib/tests/decomposition/test_r101_occurrence_ledger.py::test_r82_edge_carries_replayable_asserted_subject_and_validates_fact_identity -q`,
2026-08-19).

**Decision:** the conservation boundary is one deterministic gzip file containing a strict
schema-3 occurrence inventory, with no raw-JSON compatibility reader. The lossless TSV projection
is generated on demand and bound by `tsv_identity`, not tracked separately. The report binds the
v3 baseline, both completed runs, source release, detector, pre-resume proof,
resume dry-run, and mixed-cohort identities separately; their aggregate proof identity does not
replace those continuation bindings. One-step and closure-only stated R82 evidence remain distinct.
Malformed, partial, duplicate, mismatched, cross-axis, reversed, broken, over-depth, source-drifted,
or non-R101-changing evidence fails closed at report load as well as construction. Non-R101 delta
evidence is the canonical sorted row set bound to both run IDs and the exact SQL query identity;
its count is derived from those rows rather than accepted as an independent scalar.

Mechanical completion, content authorization, and publication eligibility are independent states.
The tracked report currently records `complete`, `pending`, and `blocked`, respectively
(`pdm run python -c 'from pathlib import Path; from ontolib.decomposition.r101_conservation import load_r101_conservation_report; r=load_r101_conservation_report(Path("ontolib/tests/decomposition/golden/neoplasm-r101-v4-conservation.json.gz")); print(r.mechanical_status,r.content_authorization.status,r.publication_gate)'`,
2026-08-19). This decision neither authorizes content nor changes D75/#271 semantics. SME pattern
review remains a final M1.6 milestone decision before publication.

## 2026-08-17 — full-corpus routing attribution requires depth-matched evidence

### D76. Recover historical v3 at depth 7 rather than compare confounded runs

The run inventory showed a full historical v3 baseline at walker depth 5 and a
completed production v4 run at depth 7, with no full depth-matched counterpart
(`docker exec ... psql ... SELECT id,status,fingerprint->>'algorithm_version',fingerprint->>'walker_max_depth',jsonb_array_length(fingerprint->'worklist') FROM decomp_run ...`,
2026-08-17). The conservation generator correctly refused that comparison with
`new run fingerprint dimension drift: walker_max_depth`
(`pdm run adjudication generate-r101-conservation ...`, 2026-08-17). Historical
recovery inputs were inspected with `git cat-file -t f2800654...`, `git diff
--name-status f2800654... -- pdm.lock pyproject.toml compose files migrations`,
and `git grep -n 'walker-max-depth|walker_max_depth' f2800654...` (2026-08-17).
The active depth-matched recovery run is
`neoplasm-d6b0df5e-aa18-4aa7-b8bb-9f8bc36c850a`, v3, depth 7, with a 15,633-item
worklist (`docker exec ... psql ... SELECT id,status,fingerprint->>'algorithm_version',fingerprint->>'walker_max_depth',jsonb_array_length(fingerprint->'worklist') FROM decomp_run ...`,
2026-08-17).

**Decision:** full-corpus routing attribution requires equal walker depth and every
other semantic dimension held constant. The minimum accepted recovery is the exact
historical v3 bytes run at depth 7 in an isolated frozen environment against the same
source, worklist, schema, and services, followed by occurrence-level comparison with
the immutable v4 depth-7 run. A v4 depth-5 run is optional evidence for a factorial
routing-by-depth interaction; it is not required for the minimum #267 claim.

The historical run is separately identified, additive evidence and introduces no
production legacy-compatibility code. Constituent conservation is not logical
equivalence; SME judgment remains a separate decision boundary. Partial runs and any
dimension-mismatched comparison fail closed.

## 2026-08-17 — exclusion roles require release-bound semantic authority

### D75. Exclusion facts remain lossless, typed, and nondefining

The runtime classification surface includes `_EXCLUDES_MARKER`,
`is_excluded_role`, `is_projectable_role`, `is_defining_role`, and `role_label`
(`rg --no-ignore -n '_EXCLUDES_MARKER|def is_excluded_role|def is_projectable_role|def is_defining_role|role_label:' ontolib/src/ontolib/decomposition/{axes.py,models.py}`,
2026-08-17). Decomposition fixtures mention R109, R110,
`Disease_Excludes_Abnormal_Cell`, and `Disease_Excludes_Finding`
(`rg --no-ignore -n 'R109|R110|Disease_Excludes_Abnormal_Cell|Disease_Excludes_Finding' ontolib/tests/decomposition`,
2026-08-17).

**Decision:** runtime exclusion-role classification is keyed by a release-bound NCIt
role-code catalog derived from the exact certified stated OWL, never by optional labels.
Labels and the EVS metadata API are corroborating drift signals and source-qualification
evidence only, not runtime semantic authority. Preserve every exclusion fact and source
occurrence losslessly, classify it as typed `negative-exclusion`, and exclude it from
positive defining axes and accepted projections. Unknown role codes remain provenance-
preserving `unknown-role/review-required` and nondefining until source evidence and an
SME decision resolve them.

R135–R142 are candidate exclusion-catalog evidence requiring exact NCIt 26.07d
stated-OWL validation and SME decision before implementation. Correct fabricated or
conflicting R109/R110 fixtures only with release-bound canary contracts. R166/R168/R170
Procedure May_Have policy is deferred while procedures remain outside disease/neoplasm
scope. #271 follows #267 as a prerequisite and must not alter its active partially
completed run; #127 final publication consumes the resulting catalog and source
qualification. This decision makes no equivalence or accepted-in-NCIt claim.

## 2026-08-14 — M1.6 release governance separates semantic gates from measurements

### D74. Semantic invariants block release; independently named metrics measure progress

The accepted #57 baseline contains 106 engine-suggestion rows, of which 48 carry the
SME `include` action; the exact-pair score is separately 80/106 precision and 80/153
recall (`pdm run pytest ontolib/tests/decomposition/test_m1_baseline.py -q`, 2026-08-14).
The include rate is immutable historical review metadata unless another human review
occurs. It is not recomputed for a changed engine and is never filler accuracy.

**Decision:** D58's semantic invariants are release blockers. Unclassified deltas,
contract or normalized-group violations, unadjudicated golden-cohort changes,
`op:PrimarySite` cardinality failures, and unexplained R101 loss block release. M1.6
must also strictly improve both current exact-pair precision and recall beyond 80/106
and 80/153 without violating an invariant. D21's `>=0.9` value remains #44's explicit
quality target; it is not the M1.6 exit gate unless a later deliberate decision makes
it one.

Five views remain independent:

1. SME include rate uses the historical 106 suggestion rows only.
2. Exact-pair precision uses current emitted NCIt-bound scoreable pairs.
3. Exact-pair recall uses the 153 NCIt-bound, non-deferred oracle expectations.
4. Full-partition agreement compares exact pair sets and partitions over the accepted
   20-concept cohort.
5. Common-pair partition agreement induces both partitions on their shared pair set
   and includes only concepts with at least two shared pairs; concepts with zero or one
   shared pair are reported separately.

The current certified NCIt 26.07d candidate has source identity `b58f48b5...`, while
the historical attested evidence has `f54dd291...`
(`rg -n 'source_identity' data/qlever-ncit/.ontoprism-ncit-candidate.json samples/ncit-26.07d-m1-sme-review.json`,
2026-08-14). Current-run evidence must therefore use a separate identity-bound
comparison contract; historical identity checks remain strict.

Frontend dependency installation is also fail-closed from this milestone onward. The
two reported high advisories are resolved at patched transitive versions, and optional
`fsevents` install scripts are explicitly denied rather than silently approved
(`npm audit --prefix frontend --json` and `npm install-scripts ls --prefix frontend`,
2026-08-14).

## 2026-08-13 — NCIt P334 values remain proposed ICD-O alignments

### D73. NCIt P334 values publish as proposed close alignments to ICD-O-3.2

**Decision:** NCIt's `P334` assertions are publisher database cross-references, not
equivalence claims. Values resolving against the exact active ICD-O-3.2 morphology
generation publish as `skos:closeMatch`, lifecycle `proposed`, with
`https://ontoprism.org/vocab#PublisherDatabaseCrossReference` justification. Publication
binds the exact P334 row fingerprint and the recertified ICD-O generation and serving
fingerprint; only the independent-evidence and reasoner-backed promotion path may produce
identity-grade mappings.

Malformed or unresolved publisher values remain explicit report rows. These additive
alignments are derived from and aligned to ICD-O-3.2 and never alter stated or decomposed
NCIt graphs. Protected term text and reciprocal ICD-O detail remain entitlement-gated.

## 2026-08-12 — Certified cross-repository publication

### D72. Publisher database cross-references remain inspectable proposals until independently validated

Uberon `oboInOwl:hasDbXref "NCIT:C…"` assertions are direct publisher evidence, but do
not state equivalence and may differ in scope across terminologies.

**Decision:** Publish each resolvable assertion as `skos:closeMatch`, lifecycle `proposed`,
with publisher-database-cross-reference justification. Bind the exact Uberon assertion
fingerprint and exact resolved NCIt target fingerprint into the report and generation
identity, and repeat both observations before publication. Preserve many-to-one and
many-to-many assertions; unresolved targets remain explicit report rows. Only the
independent-evidence and reasoner-backed promotion path can produce identity-grade mappings.

### D71. ICD-O certified repositories use separate consumer entitlement

**Decision:** ICD-O term content is served only to consumers presenting the dedicated
ICD-O entitlement. Authorization runs before repository metadata, records, search caches,
SSR rendering, or analytical reports are read.

NCIt mapping and decomposition surfaces expose ICD-O alignments only when the server's
licensed-mapping capability is enabled and the consumer presents that entitlement. The
capability does not restrict public Uberon alignments.

Publisher workbooks remain operator-supplied, access-controlled artifacts. Each served
edition/axis is an immutable PostgreSQL generation selected by an active pointer and
certified by a canonical fingerprint over exact served values. Reads carry the activation
identity used to pin the query and the certified serving identity. The ICD-O-4
topography/Uberon congruence output is an inspection report and publishes no mapping.

## 2026-08-11 — PDM commands own certified local tool configuration

### D70. Every PDM task loads repository-local Jena and ROBOT defaults

**Decision:** PDM's global script options load `.env` for every `pdm run` task and
prepend Homebrew's keg-only OpenJDK directory while retaining the inherited `PATH`.
`.env.example` defines repository-relative certified Jena 6.1.0 and ROBOT 1.9.10
installations; CI-provided variables retain precedence because PDM loads the file
without override (`sed -n '220,275p' /opt/homebrew/Cellar/pdm/2.28.0/libexec/lib/python3.14/site-packages/pdm/cli/commands/run.py`,
exit 0, 2026-08-11). A tracked contract pins the PDM option and both example paths
(`pdm run pytest backend/tests/test_integration_resource_ownership.py::test_pdm_commands_load_repo_local_certified_tool_paths -q`, one passed, 2026-08-11).

The checked local configuration resolves and revalidates both installed tools without
inline environment prefixes (`pdm run python -c` calling
`identify_jena_installation` and `configured_robot_installation`, reported `6.1.0` and
`1.9.10`, exit 0, 2026-08-11). The disposable QLever version contract and ROBOT/ELK
identity contract likewise pass through plain PDM invocations (their two focused
commands, one passed each, 2026-08-11).

**Why:** repeated manual prefixes made an omitted shell variable indistinguishable from
a code failure until deep into the grouped gate. Tool identity is project configuration,
not session state; one checked template and one task-runner boundary remove that class
of invocation error while retaining fail-closed identity validation.

## 2026-08-10 — SvelteKit owns the browser-facing SSR and BFF boundary

### D69. Route-critical reads are server-loaded through one bounded same-origin gateway

**Decision:** browsers address only the SvelteKit origin. SvelteKit adapter-node owns
HTML routing, route-critical server loads, hydration, and the same-origin `/api` BFF;
FastAPI remains the only domain API and the sole owner of QLever, PostgreSQL, and caDSR
access. NCIt, caDSR, ClinicalTrials.gov, and PubMed list/search/detail routes encode
search and pagination in the URL and render their critical data into initial HTML. The
Sigma graph remains an explicit browser-only dynamic-import island inside a stable
32-rem region (`npm --prefix frontend run test:e2e`, 14 passed, 2026-08-10).

The private BFF origin and timeout are runtime-only environment values. The gateway
rejects a mutable or credentialed origin, strips caller forwarding and hop-by-hop
headers, does not follow upstream redirects, consumes each response body within the
timeout and a 32 MiB bound, and preserves explicit upstream status and content. A built
adapter pointed at a refused local socket returned HTTP 503 with detail `FastAPI is
unreachable` (`curl -sS -i http://127.0.0.1:4174/api/v1/ncit/list` against `env
ONTOPRISM_FASTAPI_ORIGIN=http://127.0.0.1:1 ONTOPRISM_FASTAPI_TIMEOUT_MS=200
HOST=127.0.0.1 PORT=4174 ORIGIN=http://127.0.0.1:4174 node build`, 2026-08-10).

Loading UI uses one delayed accessible state with a 150 ms threshold and reserved
space. Route errors preserve HTTP status; secondary similarity, mapping,
decomposition, and related-article regions distinguish unavailable from empty and
discard stale completions. The shared-library contract passes at 98.85% lines and
91.49% branches (`npm --prefix frontend run test:coverage`, 248 passed, 2026-08-10),
and the cross-file gate reports no introduced dead-code or complexity findings (`npm
--prefix frontend run fallow`, exit 0, 2026-08-10).

**Why:** the former Vite-only proxy and route-wide SSR opt-outs made development and
production use different network paths, delayed meaningful content until hydration,
and allowed critical failures to look like blank or indefinitely loading pages. One
server-visible path makes first HTML, copied URLs, error status, security policy, and
production behavior testable as a single contract without moving domain ownership out
of FastAPI.

## 2026-08-10 — repositories and derived indexes publish certified identities

### D68. Readiness is a discriminated certification result; search and embeddings bind both proxy and content identities

**Decision:** a repository is either a typed `ready` value carrying certified identity
or a typed `unhealthy` value carrying a reason and message. The unhealthy variant has no
`source_identity` or `manifest_identity` fields. NCIt readiness binds the exact active
candidate-manifest bytes, its source identity and release, a completed journal with a
durable activation timestamp, and a live default/stated graph observation. caDSR
readiness binds persisted archive provenance to the canonical serving-row fingerprint
and count. The API and refresh UI render these variants rather than inferring identity
from reachability (`pdm run test --all`, 2,516 passed and zero skipped, 2026-08-10).

Derived NCIt search and NCIt/caDSR embedding publications record two distinct digests:
`source_identity` names the certified active proxy and `source_hash` names its canonical
ordered serving content. Readers require the active proxy identity before using a
derived index; migration 0015 deactivates legacy embedding manifests whose source proxy
cannot be proven and adds a singleton NCIt search manifest (`pdm run python
scripts/run_safe_integration.py backend/tests/test_embedding_publication_integration.py
-q`, 58 passed, 2026-08-10; `ONTOPRISM_JENA_DIR=$PWD/.tools/jena-6.1.0 pdm run python
scripts/run_safe_integration.py backend/tests/test_data_build_integration.py -q`, 10
passed, 2026-08-10).

Hierarchy browsing reads only stated direct `rdfs:subClassOf` and named genus members of
`owl:equivalentClass` intersections. It does not ask the inferred default graph for a
transitive subclass closure. Hierarchy neighbors are expanded before high-fanout roles
so the bounded graph retains its structural backbone (`pdm run
test-integration-full-store -k defined_disease_hierarchy_and_anatomy_control -v`, 1
passed against the configured 26.07d corpus, 2026-08-10).

**Why:** reachability, a release label, or a non-empty cache can each be true while the
serving proxy and its derivative refer to different builds. Keeping proxy identity and
content fingerprint separate makes both failure modes explicit and prevents a degraded
repository from emitting a plausible but unbound identity.

## 2026-08-10 — completed QLever ontology-store migration

### D67. All publisher ontology resources use certified QLever indexes

**Decision:** the application has one SPARQL implementation. Inferred and stated NCIt
share the certified NCIt QLever index; Uberon and its Cell Ontology content use a
separate certified QLever index. Postgres remains authoritative for mutable proposed
NCIt identity, revisions, lifecycle, evidence, and RDF projections (D65). There is no
runtime Oxigraph dependency (`pdm run pytest
backend/tests/test_supply_chain_contract.py::test_active_runtime_has_no_oxigraph_dependency
-q`, 2026-08-10).

The official EVS 26.07d folder exposes flat text plus stated and inferred RDF/XML OWL,
but no Turtle, N-Triples, or N-Quads (`curl -sS
'https://api-evsrest.nci.nih.gov/api/v1/ftp1/folder?folder=NCI_Thesaurus%2F'`,
inspected with `jq`, 2026-08-10). The selected build input remains the OWL pair so the
application does not substitute a different source representation. Pinned Apache Jena RIOT performs only the
streaming RDF/XML-to-N-Triples serialization step; the pinned QLever offline indexer
then builds the serving indexes.

The active NCIt proof records release 26.07d, 12,980,813 default triples, 10,855,010
stated triples, and 149,694 stated restrictions (`jq
'{ontology_version,source_identity,observation,loader}'
data/qlever-ncit/.ontoprism-ncit-candidate.json`, 2026-08-10). The active Uberon/CL
proof records 1,161,591 triples plus its Uberon, CL, and NCIt-xref sentinels (`jq
'{source_identity,observation,loader}'
data/qlever-uberon/.ontoprism-uberon-index.json`, 2026-08-10). All seven real
cross-terminology data-shape contracts pass against the two serving indexes (`env
UBERON_SPARQL_URL=http://127.0.0.1:7889 NCIT_SPARQL_URL=http://127.0.0.1:7888 pdm run
pytest ontolib/tests/repositories/xref/test_upstream_data_contract.py -m 'integration
and full_store' -v`, 2026-08-10).

The serving 26.07d index exposes 206,860 labelled NCIt embedding records through the
application's inferred-default dataset. The stated graph and QLever's unconstrained
union each expose 212,475, which is not the runtime embedding plane (`curl -fsS -G
--data-urlencode 'default-graph-uri=http://qlever.cs.uni-freiburg.de/builtin-functions/default-graph'
--data-urlencode 'query=PREFIX owl:
<http://www.w3.org/2002/07/owl#> PREFIX rdfs:
<http://www.w3.org/2000/01/rdf-schema#> SELECT (COUNT(DISTINCT ?concept) AS ?count)
WHERE { ?concept a owl:Class ; rdfs:label ?label . FILTER(STRSTARTS(STR(?concept),
"http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#")) }' -H 'Accept:
application/sparql-results+json' http://127.0.0.1:7888/`, 2026-08-10). The readiness
release guard and embedding publication count are bound to that serving release.

Three QLever-specific contracts are part of the adapter rather than caller convention:

1. QLever's unconstrained default dataset is the union of default and named graphs, so
   NCIt queries bind the internal default graph explicitly and enumerate only their
   declared plus query-referenced constant named graphs. That keeps default reads
   isolated while allowing per-run publication staging graphs.
2. QLever's Turtle upload path does not preserve RDF collection structure as the
   `rdf:first`/`rdf:rest` triples the OWL readers require. Incremental Turtle writes are
   therefore normalized to N-Triples before Graph Store upload; the disposable contract
   uses the same pinned Jena conversion as the production source build.
3. QLever returns `xsd:dateTime` at millisecond precision, so publication-marker time is
   millisecond-canonical at the type boundary. PostgreSQL intent, RDF marker, and
   crash-safe retry therefore compare one identity.

The first-install bootstrap refuses an existing target and atomically installs a
validated index. Future replacement of an existing NCIt target remains #148's
crash-safe activation responsibility; migration does not weaken that boundary.

## 2026-08-09 — standalone build-tool identity

### D66. Data-build executables are content-addressed and recorded where their output is certified

**Decision:** every non-library executable that can determine data-build output has a
`DataBuildToolIdentity` containing exactly `name`, `source`, `version`, and canonical
`sha256:<hex>` digest. Add required field `tool: DataBuildToolIdentity` to
`LoaderIdentity`; persist the ROBOT/ELK identity in `xref_run.metrics.tools`. Refuse an
xref promotion before creating its run if the configured ROBOT JAR, generated launcher,
metadata, or observed version differs from the pin.

The target shape and fail-closed behavior are enforced by the focused supply-chain and
candidate-manifest contracts (`pdm run pytest
ontolib/tests/core/test_data_build_tools.py backend/tests/test_supply_chain_contract.py
ontolib/tests/terminologies/test_ncit_sibling_store.py -q`, 2026-08-09), the real
reasoner contract (`PATH=/private/tmp/ontoprism-robot-163:/opt/homebrew/opt/openjdk/bin:/opt/homebrew/bin:/usr/bin:/bin
ONTOPRISM_ROBOT_DIR=/private/tmp/ontoprism-robot-163 pdm run pytest
ontolib/tests/repositories/xref/test_reasoner_contract.py -q`, 2026-08-09), and the
disposable-Postgres persistence contract (`pdm run python
scripts/run_safe_integration.py
ontolib/tests/repositories/xref/test_promotion_persistence.py -v`, 2026-08-09).

**Why:** a mutable tag or unchecked download lets the implementation that generated an
artifact drift while its data inputs remain unchanged. Binding the executable identity
inside the artifact's own proof makes that drift visible to every later validator.

## 2026-08-09 — bounded ontology query and NCIt curation storage topology

### D65. QLever indexes immutable publisher ontologies; Postgres owns mutable proposed NCIt

The store decision is based on OntoPrism's queries, not on whether a product includes a
reasoner or a generic graph editor. The tracked workload now binds 336 query-bearing
production definitions/constants and 103 transport operations into an executable inventory
(`jq '{query_shape_count,transport_operation_count}'
scripts/validation/sparql-inventory.json`, 2026-08-10).
Changing, adding, or removing one of those shapes changes the committed inventory digest.

The corrected exact-corpus workload returned the same NCIt release, stated restriction count,
role observation, complete-definition identity, genus walks, R82 controls, scope, detail,
search page, and neighborhood from QLever and Jena. QLever returned C27262 as 28 facts in 8
groups with identity
`9ce79377f03d6f15130d065567509a435ccb2793920c19c8846292cbf8685b5c`, enumerated
15,633 C3262 scope codes, and returned 400 neighborhood nodes/555 edges
(`pdm run python scripts/research/ncit_store_bakeoff.py qlever
http://127.0.0.1:7302`, 2026-08-09). Jena returned the identical values and identity
(`pdm run python scripts/research/ncit_store_bakeoff.py fuseki
http://127.0.0.1:7301/ncit`, 2026-08-09). On those runs QLever completed scope/search in
1.90/0.97 seconds and Jena in 11.04/5.50 seconds; Jena completed detail/neighborhood in
0.02/0.49 seconds and QLever in 0.28/5.10 seconds (same commands, 2026-08-09).

The engine-consolidation check exported the incumbent Uberon default graph as 1,194,919
N-Triples with identity
`96598b4807b62e6ee0407853b86957c5992f865272af8a2a733db40aea20f1ae`
(`wc -l tmp/bakeoff/uberon-export/uberon.nt` and `shasum -a 256
tmp/bakeoff/uberon-export/uberon.nt`, 2026-08-09). The selected QLever image indexed the
corpus in about two seconds of index work and produced a 32 MiB index whose sampled build
RSS peaked at 1,827,479,552 bytes (`docker run --rm --memory 10g ... qlever-index -i
uberon ...`; `du -sh tmp/bakeoff/qlever-uberon`; `awk 'NR > 1 && $3 > max {max = $3}
END {print max}' tmp/bakeoff/qlever-uberon/uberon.index.resource-usage-log.tsv`,
2026-08-09). All seven real upstream data contracts passed unchanged with QLever and with
the incumbent Oxigraph endpoint (`env UBERON_SPARQL_URL=http://127.0.0.1:7303 pdm run
pytest ontolib/tests/repositories/xref/test_upstream_data_contract.py -m 'integration
and full_store' -v`; same command with port `7889`, 2026-08-09). Those contracts exercise
the real version header, subclass and mixed subclass/part-of walks, NCIt xrefs, supported
CURIE boundaries, and combined NCIt/Uberon candidate generation.

The pathological complete-definition query was an application-query defect, not proof that
all open-source stores were unsuitable. The reader now queries the root first, then re-queries
the complete root-to-depth prefix only when the previous result proves a nested group exists.
It keeps every blank-node relationship inside one SPARQL result set, witnesses non-terminal
RDF list cells without comparing blank nodes to `rdf:nil`, and retains a live depth-four reject
gate (`pdm run pytest ontolib/tests/decomposition/test_complete_definition.py -v`,
2026-08-09). Search aggregates no longer reuse an input variable as their output alias, and
every limited edge query orders before `LIMIT`; the repository contract passes 21 cases
(`pdm run pytest ontolib/tests/terminologies/test_ncit_graph_store.py -v`, 2026-08-09).

**Decision.** Use a split topology:

1. **QLever is the read authority for all immutable publisher planes.** The inferred NCIt
   artifact is one index's default graph, stated NCIt is its protected named graph, and
   Uberon/CL is a separate default-graph index. Runtime entailment remains off. NCIt refresh
   builds a new immutable index and activates it only after validation; #163 owns the pinned
   packaging and migration implementation, while #148 owns crash-safe NCIt activation.
2. **Postgres is authoritative for mutable proposed NCIt.** Proposal identity, optimistic
   revision, complete lifecycle (`proposed → locally-approved → submitted → accepted-in-ncit`),
   publication/trial evidence, history, idempotent operation identity, and the exact RDF
   projection belong in one transactional plane. A committed revision and its projection are
   one transaction.
3. **Backend reads compose the planes.** List, detail, and graph-explorer reads identify both the
   QLever base source and Postgres overlay revision. A newly committed proposal is visible from
   the overlay immediately; rebuilding QLever is never the freshness mechanism for UI edits.
   An optional QLever copy is a replaceable query projection, not lifecycle authority.
4. **The Svelte graph explorer remains the curation UI.** A vendor workbench may help engineers
   inspect data, but it does not replace OntoPrism's evidence, lifecycle, conflict, filtering,
   and proposal semantics. The frontend continues to access both planes only through FastAPI.
5. **Oxigraph is not part of the target stack.** Because the production-shaped Uberon
   contracts passed without query or data changes, retaining a second SPARQL engine
   would add packaging, patching, transport, activation, and operator surface without a
   demonstrated capability benefit. D67 records the completed runtime migration.

The transport seam declares Query, Update, and Graph Store endpoints independently;
QLever is the one production profile and the standard-path profile is retained for
bounded protocol test peers
(`pdm run pytest ontolib/tests/terminologies/test_sparql_http_client.py -v`, 2026-08-09).
The tracked mutation workload completed Graph Store staging replacement, atomic decomposition
replacement, concurrent optimistic revisions, and immediate reads on both candidates
(`pdm run python scripts/research/ncit_store_mutation_bakeoff.py
http://127.0.0.1:7302`, 2026-08-09). The tracked split-topology workload produced exactly
one concurrent revision winner, reconciled revision 3 after a simulated lost response,
preserved publication and clinical-trial evidence, and emitted an 11-triple RDF projection
with combined identity
`40fd56a324c504855e9f9dd32770999683e77f60cb1c756a94881729ad5abfe0`
(`ISSUE283_POSTGRES_DSN=postgresql://ontoprism:ontoprism@127.0.0.1:7433/ontoprism
ISSUE283_QLEVER_URL=http://127.0.0.1:7302 pdm run python
scripts/research/ncit_curation_bakeoff.py`, 2026-08-09). Its `verify-only` mode returned the
same identity and lifecycle after both isolated services were force-stopped and restarted
(`docker kill ontoprism-bakeoff-qlever-ncit ontoprism-bakeoff-postgres-curation`; `docker
start ontoprism-bakeoff-qlever-ncit ontoprism-bakeoff-postgres-curation`; same curation command
with `verify-only`, 2026-08-09).

QLever must run with a server-side query timeout. The deliberately pathological depth-four
query was rejected by the selected server as HTTP 429 after 30.09 seconds
(`pdm run python scripts/research/ncit_store_bakeoff.py
http://127.0.0.1:7302 --timeout-proof`, 2026-08-09); the serving configuration preserves that bounded server
configuration and must not rely on a client disconnect to stop work.
The pinned QLever image used by the bake-off publishes both `linux/amd64` and `linux/arm64`
manifests (`docker buildx imagetools inspect
docker.io/adfreiburg/qlever@sha256:abeb20ae245184cee2991a99c22a9bb0a62f6884bb1a03747bf7e56165cb0ca6`,
2026-08-09).

**Alternatives.** Retain Jena/TDB2 as the RDF fallback: it achieved exact semantic parity, but
its slower scope/search path does not buy authority over the richer Postgres proposal model.
QLever's update and Graph Store endpoints may serve replaceable projections; the publisher
graphs remain immutable by architectural contract.
Oxigraph is rejected from the completed runtime rather than retained for Uberon: the exact
Uberon contracts above passed on QLever, so a permanent exception would be operational
duplication without observed benefit.
ROBOT plus a reasoner does not become a third canonical NCIt source; explicit ROBOT/ELK checks
remain validation boundaries. GraphDB Free remains an optional blocked reconsideration, not a
selected dependency: do not request or accept its license unless the owner explicitly reopens
this decision. The solid open-source split removes the reason to spend that license decision
now.

## 2026-08-08 — the M1 measurement landed, and what it cost to get there

### D61. Identities are bound after generation, never pre-declared

**Decision: never pre-declare the identity of an artifact that does not yet exist. Compute the
identity from the completed artifact, then record it.** The tracked row-decision export now has
a `payload_identity` and names the source, run, and engine-evidence identities in `_meta`
(`jq '{payload_identity,meta:{workbook_identity:._meta.workbook_identity,source_identity:._meta.source_identity,run_id:._meta.run_id,engine_evidence_identity:._meta.engine_evidence_identity}}' ontolib/tests/decomposition/golden/neoplasm-row-decisions.json`,
2026-08-09).

`payload_identity` is an unkeyed self-consistency digest stored beside the payload it covers. It
detects an edit only when the editor does not recompute the digest; it does not establish origin or
authorship. Loading the tracked export recomputes the digest, and the focused edit
test rejects a changed row whose digest was not recomputed
(`pdm run pytest ontolib/tests/decomposition/test_golden_review.py::test_row_decision_loader_rejects_a_hand_edited_row_set -q`,
2026-08-09).

### D62. The golden-set/corpus divergence test belongs to the full corpus, not the #154 sample

The 15-code sample is a strict subset of the 20-code adjudicated cohort; the five
adjudication-only codes are `C4791`, `C35756`, `C89995`, `C27787`, and `C115118`
(`jq -n --slurpfile sample samples/ncit-26.07d-m1-review.json --slurpfile oracle ontolib/tests/decomposition/golden/neoplasm-adjudicated.json '($sample[0].concepts|map(.code)) as $s | ($oracle[0].concepts|map(.code)) as $o | {sample:($s|length),oracle:($o|length),sample_only:($s-$o),oracle_only:($o-$s)}'`,
2026-08-09). The tracked residual comparison identifies itself as the adjudication run
restricted to that sample
(`jq '{name,denominator:(.denominator_codes|length),residual:(.residual_codes|length)}' ontolib/tests/decomposition/golden/neoplasm-corpus-comparison.json`,
2026-08-09), so it is a same-cohort check rather than an independent-population divergence test.

**Decision: retain the tracked subset comparison as a reproducible baseline, but test population
divergence only against the full corpus.**

### D63. The SME oracle lives in tracked code, not in a review workbook

**Decision: the adjudicated oracle, row decisions, engine evidence, corpus comparison, and
proposal registry are tracked under `ontolib/tests/decomposition/golden/`.** Git lists all five
inputs (`git ls-files ontolib/tests/decomposition/golden/neoplasm-adjudicated.json ontolib/tests/decomposition/golden/neoplasm-row-decisions.json ontolib/tests/decomposition/golden/neoplasm-engine-evidence.json ontolib/tests/decomposition/golden/neoplasm-corpus-comparison.json ontolib/tests/decomposition/golden/proposal-registry.json`,
2026-08-09), and the baseline test loads only those tracked inputs
(`pdm run pytest ontolib/tests/decomposition/test_m1_baseline.py -q`, 2026-08-09).

### D64. M1 is a measurement milestone; the web application precedes further content work

The engine-suggestion rows contain 48 `include`, 42 `revise`, and 16 `exclude` labels. Among
the 90 kept suggestions, 80 preserve the exact `(axis, filler)` pair, 87 preserve the filler,
and 83 preserve the axis
(`jq '[.rows[] | select(.row_type=="ENGINE SUGGESTION")] as $r | [$r[] | select(.sme_action=="include" or .sme_action=="revise")] as $k | {include:([$r[]|select(.sme_action=="include")]|length),revise:([$r[]|select(.sme_action=="revise")]|length),exclude:([$r[]|select(.sme_action=="exclude")]|length),kept:($k|length),pair_match:([$k[]|select(.engine==.expected)]|length),filler_match:([$k[]|select(.engine.filler==.expected.filler)]|length),axis_match:([$k[]|select(.engine.axis==.expected.axis)]|length)}' ontolib/tests/decomposition/golden/neoplasm-row-decisions.json`,
2026-08-09). These are different measurements: exact pair preservation does not independently
measure filler accuracy, and `include` is strictly the SME label rate. In particular, 11 of the
48 included rows differ from the recorded engine constituent in relationship-group or
`needs_review` fields
(`pdm run python -c 'import json,pathlib; p=pathlib.Path("ontolib/tests/decomposition/golden"); r=json.loads((p/"neoplasm-row-decisions.json").read_text())["rows"]; e=json.loads((p/"neoplasm-engine-evidence.json").read_text()); a=json.loads((p/"neoplasm-adjudicated.json").read_text()); E={(c["code"],x["axis"],x["filler"]):x for c in e["concepts"] for x in c["constituents"]}; A={(c["code"],x["axis"],x["filler"]):x for c in a["concepts"] if c["expected"] for x in c["expected"]["constituents"]}; I=[x for x in r if x["row_type"]=="ENGINE SUGGESTION" and x["sme_action"]=="include"]; print(sum(any(E[(x["code"],x["expected"]["axis"],x["expected"]["filler"])].get(f)!=A[(x["code"],x["expected"]["axis"],x["expected"]["filler"])].get(f) for f in ("relationship_group","needs_review")) for x in I))'`,
2026-08-09).

Candidate rows contain 63 `include` labels, but 64 kept constituents: the remaining kept row is
the `revise` decision for Stage I Endometrial Cancer FIGO 2023 (`C206219`) with
`op:PrimarySite` Corpus Uteri (`C12316`), and that revised pair is absent from
the recorded engine evidence
(`jq -n --slurpfile rows ontolib/tests/decomposition/golden/neoplasm-row-decisions.json --slurpfile engine ontolib/tests/decomposition/golden/neoplasm-engine-evidence.json '[$rows[0].rows[]|select(.row_type=="ADD IF MISSING")] as $r | {include:([$r[]|select(.sme_action=="include")]|length),kept:([$r[]|select(.sme_action=="include" or .sme_action=="revise")]|length),revised:([$r[]|select(.sme_action=="revise")|. as $x|{code,expected,in_engine:any($engine[0].concepts[];.code==$x.code and any(.constituents[];.axis==$x.expected.axis and .filler==$x.expected.filler))}])}'`,
2026-08-09).

The tracked NCIt-bound score has 153 expected pairs, precision 0.7547, recall 0.5229, and
relationship-group agreement on 2 of 20 concepts
(`pdm run pytest ontolib/tests/decomposition/test_m1_baseline.py::test_expected_pair_provenance_holds_the_m1_baseline ontolib/tests/decomposition/test_m1_baseline.py::test_ncit_bound_precision_and_recall_hold_the_m1_baseline ontolib/tests/decomposition/test_m1_baseline.py::test_group_partition_agreement_holds_the_m1_baseline -q`,
2026-08-09). The integer true-positive count is uniquely 80: it is the only integer `tp` for
which `round(tp / 153, 4) == 0.5229`
(`pdm run python -c 'print([tp for tp in range(154) if round(tp/153,4)==0.5229])'`,
2026-08-09).

**Decision: M1 is "decomposition baseline measured against SME truth". Engine-quality work
follows the measurement; web-application work precedes further content work. Keep subsequent
work on one small issue branch apiece.**

## 2026-08-07 — what we produce is NCIt, and provenance is what makes alignment work

### D60. Everything OntoPrism emits is NCIt in a new rendition; derivation is provenance, never ownership

Recurring design discussions stalled on the phrase "external content". A concept or relation
sourced from Uberon, Cell Ontology, SNOMED CT or ICD-O-3 was repeatedly treated as *belonging*
elsewhere, which turned a question about where something came from into a question about whose
it is. Those are different questions, and conflating them produced a false category — content
that is permanently foreign — that this project's architecture neither has nor wants. Several
downstream decisions inherited the error before it was caught.

**Decision.**

1. **Everything we add, change or remove is NCIt.** The deliverable is NCIt reorganised, not NCIt
   blended with other ontologies. That is what makes it adoptable: NCI can take it whole. A
   concept or role we introduce is NCIt content even when it exactly matches, and was directly
   derived from, a class or property in another ontology.

2. **All of it is provisional until NCI adopts it.** New content is a *proposal*, not a private
   extension. The lifecycle already exists and is authoritative: `proposed → locally-approved →
   submitted → accepted-in-ncit`. `locally-approved` means our SME accepted it; it does not mean
   NCI did. No status short of `accepted-in-ncit` may be presented as NCIt-authored.

3. **Derivation is recorded as provenance and alignment, never as ownership.** Emitted definitions
   use NCIt identifiers for their concepts and fillers. Where a concept or relation of ours
   corresponds to one in a corroborating terminology, that identifier is a mapping or provenance
   annotation alongside our content, never an authored definition filler — the pattern
   `AxisContract.ro_parent` already uses, where `op:PrimarySite` is *our* relation and `RO:0004026`
   is what it aligns to (D38, D60).

4. **Provenance is instrumental.** It exists so that Metathesaurus integration and cross-terminology
   mapping actually work, and so a reviewer can see what evidence supported a proposal. It is not
   an audit ritual. Provenance that no mapping or reviewer consumes is not worth the field.

5. **Alignment is a design goal, subordinate to architectural integrity.** Align with expert-curated,
   well-vetted sources as far as they take us — and stop where alignment would cost a core design
   property. Adopting an external modelling error, or a dependency that stops NCIt resolving on its
   own, is not alignment.

**Why.** Two ontologies can converge on the same concept without either owning it; identity and
derivation are orthogonal. Treating derivation as ownership creates content we can never propose
back to NCI, which defeats the purpose of building it. Treating it as provenance creates exactly
the mapping trail that makes the result useful in the Metathesaurus — the same evidence serves both
governance and interoperability.

**Language rule, because the wording is what caused the drift.** Do not write "external content",
"borrowed from", or "depends on" about anything we emit. Write "derived from", "aligned to",
"corroborated by", or "proposed, evidenced by". If a sentence implies another project owns
something in our output, it is wrong regardless of intent.

**Consequence for the semantic-bundle layer.** A member NCIt does not currently assert is
`proposed`, carrying its evidence — not "externally asserted". `SemanticBundleMember` and
`ProjectedConstituentEvidence` carry no lifecycle status today, while `GoldenConstituent` does;
that gap is what allowed the wrong framing to look reasonable. Reuse the existing `PairProvenance`
vocabulary rather than adding a fourth copy of it.

## 2026-08-04 — the candidate M1 oracle covers audited R103/R108 without laundering held axes

### D59. Contracted-role source audit governs projection scope and relation-specific collapse

The latest technical #57 review found that the candidate oracle and production walker
shared the same omission: inherited R103/R108 facts were filtered before curation, while
R104/R107 contracts existed in code but had never received axis-level SME sanctioning. A
certified NCIt 26.07d complete-definition pass over all 20 concepts found 304 contracted-role
facts.
After same-axis specificity and reviewed generic suppression, v10 still omitted 13 R103,
25 R108, 10 R104, and one R107 pair. The old 0.6807 recall therefore measured an
engine-shaped reference, not the source-complete content now presented for final v14
review.

**Decision:** the pending v14 M1 candidate includes the reviewed R103 and range-valid R108
survivors after same-axis collapse and the versioned `contracted-role-generic-v2` list.
Corrections are validated against a matching root/filler source occurrence, not a
role-specific occurrence. The pair scorer becomes usable after the workbook's main
attestation; `stage-bundle-pilot finalize` separately requires complete semantic-bundle
attestation before semantic rules become `ATTESTED`. NCIt marks R103 as non-defining in
P98, so its 12 expected pairs remain scored and reports derive their visible
`non-defining` stratum from the axis contract. The versioned
`ncit-26.07d-unsupported-filler-v1` register excludes R103 C54105 for C102870 and C27787:
that germinal-layer filler conflicts with both concept definitions and is not accuracy
content. R104 CellOrigin and R107 CytogeneticAbnormality remain explicit named scope
omissions until their cardinality, grouping, RO alignment, and suppression policy are
adjudicated; the 10 R104 and one R107 survivors are not silently called non-core or mixed
into current metrics. The walker projects inherited R103/R108 within its depth-five
projection bound and continues to hold inherited R104/R107. The independently bounded
complete record retains all source facts regardless of projection suppression.

The generic list is role-specific: R103 C45714; R104 C12578; R105 C12917, C12922,
C36779; and R108 C36115, C53596, C54172. Membership is inherited coverage over the
complete stated record, measured per role: v1 wrongly suppressed R108 C36122 (Benign
Cellular Infiltrate) on frequency drawn from R142 exclusions on malignant concepts. As a
positive R108 finding it is asserted only on the benign genera C3677, C4776 and C5111 and
covers 5.6% of the cohort against 61-100% for every retained entry, so v2 restores it and
Left Atrial Myxoma (`C4791`) regains a true constituent. Changes require a new list version and
the full-corpus routing impact gate from D58. Runtime stage system/value routing uses the
versioned `ncit-26.07d-stage-kind-v1` reviewed code allowlist. Definitions informed its
curation but are not consulted at runtime; semantic type alone is insufficient because
NCIt uses `Classification` for both values and frameworks.

`rdfs:subClassOf` licenses most-specific collapse on routed axes except
`op:AssociatedLineageClassification`, whose fillers remain independent. R82 part-of
licenses collapse only on location axes under RO:0004026; it is not subsumption for
morphology, cell, tissue, finding, or classification axes. Consequently C35756's
C12704/C12705 lineage classifiers remain separate and ungrouped. Complete-definition groups preserve
source co-assertion partitions; curated projection groups may additionally identify
multiple co-equal values retained on one routed axis, but never genus-walk path bookkeeping.

The three PrimarySubsite mappings and C206219's R100-to-PrimarySite override remain
scoreable provisional human-curated morphology-context mappings. They preserve original
source roles and are not claimed as OWL-derived partonomy. PrimarySubsite has an
AssociatedRegion fallback; the R100 override has no machine-readable fallback contract.
C132677 currently emits only R108 C48322 as a ClinicalFinding. A future class-level
projection may derive a separate non-constituent `unknown-cup` status. C198031 retains
C3168 alone because C4005 is its broader stated defining ancestor.

**Why:** rules and conserved source dispositions make the candidate reproducible and
expose production blind spots without importing unadjudicated axes. The pending v14
descriptive NCIt-bound result is TP 80, FP 26, FN 73 (precision 0.7547, recall 0.5229);
the augmented view adds one locally approved expectation and therefore has FN 74 (recall
0.5195). These are packet-verification values, not authoritative performance claims, until
both attestations are complete. `ncit_bound` filters the expected set to NCIt-stamped
pairs; actual engine emissions have no pair-level provenance and all remain in its actual
and false-positive denominator.

## 2026-08-03 — cardinality, local-relation maturity, and routing release gates

### D58. Primary-site semantics, provisional relations, and routing gates are explicit

The M1 adjudication needed to distinguish three cases without introducing another
pre-coordinated disease class: a patient with one cancer, a patient with concurrent
independent primary malignancies, and one cancer with metastatic lesions at additional
locations. It also introduced two local relation terms whose fillers are ordinary NCIt
26.07d concepts, unlike a locally minted filler that the source-bound engine cannot emit.

**Decision:** primary-site semantics have three explicit levels:

1. An NCIt disease-class projection has `op:PrimarySite 0..1`. Absence usually means that
   primary site is not a defining dimension of the class; it does not imply unknown
   primary.
2. A cancer-disease occurrence (one neoplastic process in one patient) also has
   `op:PrimarySite 0..1`. The future occurrence model enforces this same invariant.
3. A patient has `0..*` disease occurrences and therefore no patient-level maximum on
   primary malignancies. Synchronous multiple primary malignancies are separate
   occurrences, each with its own primary-site state.

`op:PrimarySite` remains anatomy-valued and never receives an unknown sentinel. Every
disease occurrence instead carries exactly one conceptual `primarySiteStatus` from the
closed set `known`, `unknown-cup`, `undetermined`, and `not-applicable`; `known` holds if
and only if a `PrimarySite` filler is present. `unknown-cup` is a positive clinical state,
not absence, and is accompanied by an explicit unknown-primary finding where supported.
`undetermined` preserves incomplete workup or unresolved second-primary-versus-metastasis
classification. `not-applicable` covers classes or occurrences that genuinely do not pose
the site question. A future class-level projection may derive `known` from a present site,
`unknown-cup` from explicit complete-record unknown-primary evidence, and otherwise
`not-applicable`; the current extractor neither computes nor persists this status. A
future class-derived value never propagates to an occurrence. A solid-tumour occurrence
from a site-agnostic class defaults to `undetermined` until site or CUP status is
established; occurrence-level `not-applicable` is reserved for a disease model where
primary site truly does not apply.
Issue #263 tracks the occurrence schema/API and executable invariants.

Occurrence individuation is upstream and out of scope. The 0..1 rule must never decide
whether two involved sites are two primaries or one primary with metastasis. A future
instance model must preserve an unresolved site role rather than coercing it to
`MetastaticSite`. The same occurrence may have `0..* op:MetastaticSite` values once that
relationship is established. `op:PrimarySubsite` refines anatomy within one primary-site
umbrella and never denotes another primary. Predisposition syndromes attach to occurrences
through the R126 relation family; they do not relax primary-site cardinality.

`op:PrimarySubsite` and `op:AssociatedPriorDisease` are provisional local relations with
stable IRIs, machine-readable effective/review dates, a named review trigger, evidence
counts, and fallback encodings. `op:PrimarySubsite` is declared under verified
`RO:0004026` and falls back to `op:AssociatedRegion`; `op:AssociatedPriorDisease` remains
explicitly unaligned and falls back to raw `R126` with `needs_review=true`. The former has
three cohort examples and low migration risk; the latter has one and must not silently
generalize to treatment causation or shared susceptibility. Its current name is retained
provisionally because the formal definition is less ambiguous than `FollowsDisease` or
`AfterDisease`; the same review trigger covers both name and definition. Both relations
remain in the NCIt-bound score because their fillers are NCIt 26.07d concepts; relation
locality and filler locality are independent provenance dimensions. The artifact binds the
whole proposal registry identity separately. Its augmented view adds endpoint proposals,
such as the locally approved `MINT-781c8c8c6096` filler, only when the expected row names
that proposal ID and status.

Provisional unaligned `op:` relations are a time-bounded exception to the project's OBO
Foundry Principle 7 target. They may support the curated local projection only with
machine-readable maturity, review trigger/date, evidence, and fallback; they emit no
speculative RO parent axiom and are not represented as Principle-7-conformant. This
exception also covers `op:AssociatedLineageClassification` until a classification-type RO
parent is established.

Routing-semantic changes use two gates. Focused tests and the adjudicated sample are the
development/PR inner loop. Before release, the algorithm identity changes and a
full-corpus impact report classifies every changed pair. More than one primary site,
contradiction between a projected site and derived class status, contract/group
violations, or an unadjudicated change to the golden cohort blocks release. Loss of a
previous primary site requires named source evidence and re-adjudication; absence alone is
never classified as CUP. Every stated R101 fact must be conserved as a routed pair, a
collapse under a named ancestor, or a named suppression. Unclassified deltas require a
written reason; acceptance is semantic, never a percentage threshold.

The depth-5 walker bound remains an identified production constraint, not an oracle
boundary. Omissions attributable to it are explainable implementation limitations but
still score as false negatives against the source-complete reference and lower recall.

C100051's prior-disease normalization is source-bound: the NCIt 26.07d class definition,
not R126 itself or external clinical literature, states that the renal cell carcinoma
develops in long-term survivors of childhood neuroblastoma. Without that concept-specific
source wording, the assertion would remain raw R126 and review-required.

**Why:** a total anatomy relation would misclassify every site-agnostic morphology class as
CUP or corrupt the anatomy range with a sentinel. A separate closed status preserves the
FHIR/ISO-style null-flavour distinction between unknown, undetermined, and not applicable
without weakening `PrimarySite 0..1`. Patient-level multiplicity and occurrence-level site
cardinality are compatible once diagnoses are post-coordinated occurrences rather than one
combined disease concept. Keeping local relations in the bound score prevents the metric
from becoming blind to the hardest adjudicated distinctions. Routing failures are silent
and clinically plausible, so only versioned corpus-wide conservation and invariant checks
can expose systematic loss outside the small golden cohort.

## 2026-08-02 — missing concepts and relations remain typed proposals

### D57. Proposal governance is source-bound, duplicate-checked, and non-promoting

Issue #57 exposed two separate gaps that the former qualifier-only mint record could not
represent safely: a missing atomic NCIt concept and an overloaded NCIt source role that
may require several univocal relations. The old `MintedConcept` payload stores only axis,
label, source signal, and status. It cannot carry a formal definition, parentage,
domain/range, duplicate-search evidence, external-review target, or source examples, and
therefore cannot support a defensible NCIt or Relation Ontology submission.

**Decision:** proposal review uses a strict versioned registry with two discriminated
types: concept proposals and relation proposals. Both carry an identified NCIt source,
formal definition, source roles, rationale, one or more resource- and version-labelled
duplicate checks, a submission target, and an explicit `proposed`, `locally-approved`,
`submitted`, `accepted`, or `rejected` state. Concept proposals additionally carry their
projection axis, parent concepts, semantic types, synonyms, and source concepts. Relation
proposals carry their normalized projection axis, domain, range, and representative source
pairs. Accepted concepts resolve to an NCIt code; accepted relations require both an
assigned absolute IRI and its ontology version. IDs are deterministic from the concept
axis/name or relation name; duplicate IDs and duplicate-check resources fail closed. JSON
loading rejects duplicate keys and verifies a canonical SHA-256 registry identity. Every
augmented golden expectation names its proposal ID, and import verifies the registry
identity, source, release, proposal status, axis, and concept filler where applicable.

Only `proposed` records enter deterministic flat submission exports. The same export
command also writes locally augmented RDF and accepted replacement resolutions, but
export does not automatically promote registry records. A separately ratified relation
may enter `AXIS_CONTRACTS` and production routing through an explicit code change; no
proposal is approved merely because an export exists. `pdm run
adjudication export-proposals REGISTRY OUTPUT_DIR` validates the registry before writing
NCIt concept, relation, manifest, augmentation, and replacement artifacts.

**Why:** a missing class and a missing relation are different ontology changes, and an
overloaded relation must not be "fixed" by inventing a class. Source-bound duplicate
evidence prevents proposals for concepts already present under another label or semantic
type; the explicit non-promoting boundary keeps research candidates filterable and
submission-ready without laundering them into accepted ontology content.

## 2026-07-30 — every completed work item has an explicit classification

### D56. Persist typed non-decomposition outcomes and all observed semantic types

The certified M1 SME-review run exposed an ambiguity that aggregate coverage could not
explain. `C162770` was correctly excluded because its NCIt P106 type is `Finding`;
`C102883` was an applicable `Neoplastic Process` that was correctly atomic. Both had
previously been persisted as the same all-null/false, zero-count work-item shape.

**Decision:** every completed work item stores one closed outcome:
`decomposed`, `residual`, `semantic-excluded`, or `atomic-no-op`. Historical completed
rows whose reason cannot be recovered are backfilled as `unknown`, never guessed. The
row also stores the complete canonical set of observed source semantic types; the
single `semantic_type` column remains a compatibility projection. Database constraints
bind each outcome to its decomposed/residual flags, and incomplete items cannot carry a
completion outcome.

Run metrics and API summaries report each outcome count, while the ordered run-outcomes
API exposes the per-concept classification and source types. For a completed current
run, `total_in_scope` must reconcile exactly with decomposed + residual +
semantic-excluded + atomic-no-op; `unknown` is expected only for migrated history.

**Why:** the hierarchy worklist is broader than the algorithm applicability gate.
Coverage alone must not conflate “not handled by this algorithm” with “handled and
already atomic,” and a later reader must not reconstruct either fact from nulls. This
keeps sample review and full-corpus interpretation evidence-bearing across fresh,
resumed, and historical runs.

## 2026-07-30 — R82 closure bounds include real definition-filler cones

### D54. Calibrate inherited-superclass depth to the certified C27262 sample

#213 selected an eight-hop inherited-superclass bound from an owned synthetic chain and
proved the constant-subject R82 expansion query safe. The first source-qualified 26.07d
sample run later failed closed on C27262: one of its five comparison-filler superclass
cones needs fourteen hops before termination. The eight-hop policy therefore prevented a
certified production-shaped concept from reaching a verdict even though the query shape
and every independent resource cap remained well within their envelope.

**Decision:** permit at most fourteen inherited named-superclass hops while retaining
#213's eight-hop R82-to-R82 limit, one-attempt constant-subject queries, 16-code request
tiles, 64-request limit, 256 expanded-code limit, 256-row response sentinel, 4,096 total
rows, and 64 KiB query-body limit. Exhaustion errors report the configured bound rather
than a hard-coded historical number.

On the certified source identity
`f54dd2910a31245a30cea094dc72ce6a5c8d7b5a9c4e484007a35a1c343624c8`,
C27262's closure terminates at fourteen hops with no requested R82 pair, using 11
requests, 40 rows, and 35 expanded codes. Thirteen hops still fails closed, establishing
boundary liveness; a following source query remains healthy at NCIt 26.07d.

**Why:** a safety limit that rejects a canonical production case is not empirically
calibrated. Raising only the measured depth dimension to the smallest sufficient value
preserves the bounded algorithm and leaves substantial independent request, row, and
memory headroom.

## 2026-07-30 — review samples are explicit source-bound worklists

### D55. Persist the canonical stratified sample definition in run identity

`--total-limit` is a deterministic truncation, not a stratified sample: it cannot
guarantee coverage of rare staging editions, semantic exclusions, deep genus DAGs,
multi-valued/grouped definitions, NLP/mint paths, region/organ resolution, or atomic
controls.

**Decision:** a decomposition review sample is a strict, tracked JSON manifest containing
its schema, name, branch/root/scope contract, D47 source identity, ontology version,
selection method and optional seed, plus an exactly ordered unique code list. Every code
has sorted overlapping stratum tags and a non-empty rationale; the schema requires the
complete review-stratum catalogue. The canonical 26.07d M1 manifest is
`samples/ncit-26.07d-m1-review.json`, identity
`01df2e8803d3e8cea0c3c364bbd60581f7464261b8541fa9816bb4fdeb8ed430`
(`pdm run python -c 'from pathlib import Path; from ontolib.decomposition.sampling import load_sample_manifest; print(load_sample_manifest(Path("samples/ncit-26.07d-m1-review.json")).identity)'`,
2026-08-10). The worklist is bound to the QLever-backed D47 source; the separately
adjudicated SME sample and its derived evidence retain their original source binding.

Every invocation revalidates the live D47 source, proves the manifest release and source
identity match, enumerates the complete hierarchy scope, and rejects any selected code
outside it before creating or reopening provenance. The manifest order is the persisted
worklist. Sample runs require a file output and reject `--load`, `--total-limit`, and
equivalence emission, keeping review separate from publication. Ordinary `--total-limit`
smoke runs also reject `--load`: a truncated worklist may produce a diagnostic artifact,
but it must never replace the complete public graph. Sample runs' schema-v3 run fingerprint
and resume identity bind the manifest digest; ordinary and historical runs remain schema
v2 with their existing canonical digests. The configuration version is
`nested-definition-v2`, preventing pre-D50/D55 work from resuming under the complete
nested-definition reader.

**Why:** reproducibility requires the selected cases and the source they were selected
from, not merely a random seed or release label. Binding the exact reviewed definition to
resume prevents a changed rationale, order, source, or stratum set from masquerading as
the same scientific run, while review-only execution prevents a sample from accidentally
becoming production data.

## 2026-07-30 — normalized axes have executable semantic contracts

### D52. Preserve source roles while serving only univocal projection relations

The certified 26.07d stated artifact exposed a factual error in D23's old prose:
`R108` is `Disease_Has_Finding`, `R104` is `Disease_Has_Normal_Cell_Origin`, and
`R111`–`R116` (plus `R89`) are the probabilistic `May_Have_*` family. In particular,
`R114` is `Disease_May_Have_Cytogenetic_Abnormality` and `R115` is
`Disease_May_Have_Finding`; they are not the defining clinical-finding and cell-origin
roles formerly assigned to them.

**Decision:** the curated projection routes every supported positive NCIt role to a
single-sense `op:` relation. Each relation has an executable contract containing its
human label and definition, NCIt-backed domain/range codes and labels, provenance, and
source-role mapping. Direct mappings include `R88 → op:StageValue`,
`R100 → op:AssociatedSite`, `R101 → op:PrimarySite`, `R102 → op:MetastaticSite`,
`R103 → op:NormalTissueOrigin`, `R104 → op:CellOrigin`, `R105 → op:CellType`,
`R106 → op:MolecularAbnormality`, `R107 → op:CytogeneticAbnormality`,
`R108 → op:ClinicalFinding`, and `R110 → op:Grade`. Contextual splits retain the same
source role: R88 stage systems use `op:StageSystem`; R101 regions and
lineage classifications use their existing dedicated axes.

Every role-derived constituent stores `source_role` separately from `axis`. Unknown
roles retain their NCIt code and always require review; probabilistic R89/R111–R116 and
negative `Excludes_*` restrictions do not enter the curated projection but remain in
D50's complete definition. Migration `0010_constituent_source_role` carries the
provenance through PostgreSQL. Turtle publishes the axis contracts and
`op:sourceRole`; the read API preserves it, and `GET /api/v1/decomposition/axes`
serves the catalogue without requiring a database.

**Why:** a raw source identifier is provenance, not a semantic contract. Keeping it
separate makes normalization reversible while preventing ambiguous or factually
misidentified NCIt roles from silently becoming the public composition grammar.

## 2026-07-30 — decomposition branches are executable contracts, not run labels

### D51. Separate a branch's hierarchy population from its decomposition algorithm

`neoplasm` and `disease` previously selected the same semantic-type population and the
same axis-qualified algorithm; the free-form label only changed run identity. That was a
false interface promise, but removing `disease` was also wrong: NCIt's `Neoplasm`
(`C3262`) is a descendant of `Disease or Disorder` (`C2991`), and hierarchical concepts
may legitimately share an algorithm while selecting different populations. Semantic
types are not a hierarchy oracle: the certified stated corpus contains hierarchy members
outside the canonical three semantic types and concepts with those types outside the
disease hierarchy.

**Decision:** a closed `DecompositionBranch` accepts `neoplasm` and `disease`. Each
executable specification owns an immutable hierarchy root and scope algorithm:
`neoplasm → C3262`, `disease → C2991`, both at
`stated-genus-subclass-v1`. Scope is the strict descendant closure of the stated named
class DAG, combining named `rdfs:subClassOf` edges with named genus members from
`owl:equivalentClass/owl:intersectionOf`. The bounded definition-list reader fails
closed if a later named genus exists. The disease population therefore contains the
neoplasm root and every neoplasm descendant; the neoplasm worklist excludes its own
scope anchor.

Both branches deliberately dispatch to the same `axis-qualified` algorithm and canonical
semantic-type applicability gate (`Neoplastic Process`, `Disease or Syndrome`,
`Cell or Molecular Dysfunction`). The hierarchy defines which concepts are considered;
semantic type determines whether this algorithm applies to a considered concept. Scope
root and scope-algorithm version are persisted in the fingerprint, so historical
pre-hierarchy runs cannot resume and a future hierarchy change cannot masquerade as the
same run. `regimen` and arbitrary labels fail before provenance is created. Regimen stays
unavailable until its separate component-bag mini-design is implemented end to end.
Completed historical rows retain their original free-form label on the read-only summary
API.

Run completion persists one stable metric schema, including the residual numerator and
rate. The run-summary API exposes every accepted stored metric: worklist/decomposition,
residual, mint, complete-definition, projection-loss, coverage, and historical
round-trip fields. Fresh and resumed runs both reconstruct and serialize through this
same completion path.

**Why:** a branch is meaningful when it changes an executable contract—population,
algorithm, or both. Keeping population and algorithm explicit avoids cosmetic labels
without forcing different hierarchy levels to invent different decomposers. A closed
boundary also ensures that adding regimen later requires an explicit algorithm dispatch
and its distinct contracts.

## 2026-07-30 — decomposition publication is journaled and reconcilable

### D53. Commit each system at its native boundary; reconcile marker-ahead retries

PostgreSQL, a filesystem, and Oxigraph cannot participate in one atomic commit. Marking
the run complete before publishing the file and graph exposed a false success, while a
Graph Store `PUT` after completion had no durable identity or recovery protocol.

**Decision:** render the complete Turtle to a same-directory staging file, flush and
`fsync` it, parse it, and prove its exact concept set and run links before publication.
Its SHA-256 is the representation identity. A session-level PostgreSQL advisory lock
serializes publishers across the external coordination window; the run journal stores
an immutable representation identity, destination, build time, attempt count, and
bounded publication-only failures separately from processing failures.

When `--load` is requested, upload the artifact to a unique run-scoped staging graph.
One Oxigraph SPARQL Update clears the public decomposed graph, adds the staging graph,
drops it, and inserts a marker containing run ID, D47 source identity, representation
identity, and build timestamp. The pinned real Oxigraph contract proves that the update
is transactional, including clean replacement by an artifact with no decomposed
concepts. SPARQL Update transport failures are never blindly replayed: the marker is
read first to determine whether the server committed.

After graph publication, atomically replace the file and `fsync` its directory; only
then may PostgreSQL mark the run and publication `complete`/`published`. A crash can
therefore leave the graph and/or file ahead of PostgreSQL, but never a partially replaced
public graph or a completed database row ahead of requested publication. A matching
marker-ahead retry replays the sealed validated graph to repair possible drift,
republishes the same bytes to the file, and completes the journal. A journal-ahead retry
may replace only the exact predecessor marker captured with its immutable intent. A
different, malformed, or uncaptured predecessor fails closed. Historical complete runs
are labelled `legacy`, not retroactively certified.

**Why:** cross-system atomicity would be a false guarantee. Native atomic replacement,
an immutable intent, and idempotent state-based reconciliation provide the strongest
honest contract while retaining the last complete public graph until one complete new
graph commits.

## 2026-07-30 — the complete stated definition is the decomposition record

### D50. Persist the stated definition DAG separately from its curated projection

The curated constituent view intentionally filters, routes, and collapses stated facts.
It therefore cannot serve as the source of truth for reconstruction, and storing only
that view made `group`/`needs_review` lossy across PostgreSQL and the read API.

**Decision:** for every decomposed concept, breadth-first read every
`owl:equivalentClass`/`owl:intersectionOf` member from the protected stated graph,
following every named genus that is itself defined. Anonymous nested intersections are
canonical group nodes with explicit child-group edges and root-group identities;
atomic typed genus and existential-restriction facts attach to those groups with their
anchoring concept and named-genus DAG depth. Recursive structural SHA-256 identities
ignore blank-node labels and semantically irrelevant intersection order while preserving
the nested group graph. One compact subject-anchored query returns the reachable RDF
list cells; application validation bounds the accepted record to 64 members per
intersection, anonymous nesting depth 4, named-definition depth 64, and 4096 scheduled
genera. It queries each reconvergent named genus once and fails closed on overflow,
gaps, disconnected cells, missing nested groups, list/group cycles, foreign IRIs,
conflicts, or store failure. It never reconstructs from inferred
`rdfs:subClassOf+`.

The existing constituent view remains useful, but is now an explicit projection whose
role- and parent-derived members link back to source definition-fact IDs; NLP fallback
members that mint retain separate proposal provenance. Complete/projected/lost fact
counts are run metrics. PostgreSQL migrations `0009_complete_definition`,
`0012_nested_definition_groups`, and `0014_definition_presence` store the typed facts,
canonical groups/edges, explicit empty-definition presence, and source links atomically
with each fenced work item; invalidation removes them in the same transaction. The
additive RDF artifact carries the same facts, group graph, root-scoped occurrence
identities, counts, review flags, and trace links. The read API round-trips constituent
`group`, `needs_review`, and all source-fact IDs.

The version-pinned stated-store contract proves the real shapes independently:
`C3879` has the two stated genera `C160980` and `C4815`; `C136775` preserves a traced
co-equal region group; and `C27787` preserves a traced review-required `R105` pair.
It also exposed that the C6135 morphology-to-organ tiebreaker had short-circuited D20
and erased the associated regions from the projection. Issue #156 corrected that
cross-axis collapse: the known organ remains `R101`, while every co-present non-organ
fact is routed to `op:AssociatedRegion` and traced to this complete record.

A later source-qualified 26.07d build corrected the original direct-member assumption:
97 concepts contain an anonymous nested intersection member, 91 of which are in the
inferred neoplasm scope. C27262 has eight direct outer members plus three restrictions
in one nested group. The old six-position detector walker both rejected that group and
could not reach the final two direct restrictions. Detection now derives its roles from
the same complete record instance used by projection and provenance, so structural
coverage cannot drift between two readers. The detector's configurable walker-depth
bound limits only that legacy role projection; it never truncates the independently
bounded complete record.

**Why:** completeness and a convenient navigation view are different artifacts. Keeping
both makes every curated omission measurable and traceable without weakening the
additive, non-destructive model. This is the only representation future equivalence or
round-trip fidelity may consume. It does **not** itself enable equivalence emission or
claim a fidelity score; D43's quarantine remains until a separate proof/validation step
defines and verifies those semantics.

## 2026-07-29 — the mutating reviewer runs alone

### D49. `pr-test-analyzer` runs on its own, never beside another reviewer

The mandatory pre-PR review requires a clean worktree and a review of the committed
`main...HEAD` diff. `pr-test-analyzer` earns its verdicts by mutating production code to
prove a test fails when the code is wrong. Running it beside the other four therefore
breaks their precondition, in any round. Observed on PR #236 round 3: three reviewers
independently reported a dirty worktree and a `finish_run` body that did not match the
commit under review — a *second*, out-of-band mint promotion on its own connection, ahead
of the run-state gates, which no commit on the branch contained. (The legitimate
promotion inside that transaction, after the gates, is D48 and was present throughout;
the entry is about the duplicate, not about D48.) It was `pr-test-analyzer`'s mutation,
reverted within a minute, but each of the three had to spend its round establishing that
the code it was reading was not the code under review, and one filed it as a Critical
finding.

**Decision:** in every round, run the other non-converged reviewers in parallel and
`pr-test-analyzer` alone against the same commit. It must restore every mutation; the
orchestrator then verifies `git status --porcelain` is empty **and** `HEAD` is unchanged
before accepting the verdict, and treats a failure of either check as an inconclusive,
non-converged run. Convergence bookkeeping is otherwise unchanged.

**Why:** a reviewer that cannot trust the worktree cannot produce a verdict about the
diff, and a mutation observed by a bystander looks exactly like a real defect. An empty
`git status` alone is not proof of restoration — a mutation that was committed or amended
into `HEAD` leaves the tree clean while the reviewed diff has moved. Serializing the one
agent that writes costs a single extra round-trip and removes a whole class of false
Critical findings — cheaper than re-running the four it disturbs.

## 2026-07-29 — decomposition runs are exact source-bound state machines

### D48. Materialize the worklist and fence every persisted concept result

The former run ID used second-resolution timestamps, resume inferred progress from
constituent rows (so zero-output concepts disappeared), and a concept's constituents and
minted proposals committed separately. The manifest recorded only `owl:versionInfo`;
tests could therefore agree with one another while a retry crossed a different NCIt
snapshot or a failure exposed partial results.

**Decision:** every run uses a branch-prefixed UUID and atomically persists an immutable,
canonical fingerprint plus the exact ordered worklist. The fingerprint binds D47's stable
source identity, branch, semantic-type scope, selected worklist and limit,
algorithm/config versions, walker depth, output/load modes, and one stable emission
timestamp. PostgreSQL constrains run/work-item states and rejects identity mutation.

Each non-complete work item is claimed with a fresh fencing token. Replacing that
concept's constituents and run-scoped mint proposals and marking it complete is one
transaction; failures roll back before bounded error evidence is recorded. Resume is
permitted only for a matching running/failed manifest, never re-enumerates, and processes
exactly non-complete work. Metrics and normalized TTL are reconstructed from the full
worklist, so fresh and resumed completion agree. Mint proposals enter the global curator
queue only when the run completes. The D47 manifest and full live candidate observation
are required before work, and source drift before publication invalidates every persisted
result row for the run in one transaction. The `--out` TTL is rendered to an unpublished
staging sibling and source identity is rechecked before the publication protocol begins.

**Why:** a successful manifest now proves one complete computation over one identified
NCIt snapshot; interruption, collision, retry, and zero-output cases cannot masquerade as
complete or mix source states. D53 owns the multi-system publication and reconciliation
contract.

## 2026-07-29 — NCIt stores are constructed and certified as inactive siblings

### D47. Build on the serving filesystem with the pinned CLI; activation is a separate capability

The same-release artifact proof in D46 does not prove that the inferred file was loaded
into the default graph, the stated file was loaded into its protected named graph, or the
resulting RocksDB store is compatible with the serving executable. A hand-built test
double initially placed `--graph` before the Oxigraph `load` subcommand; the real 0.5.3
CLI rejected that order, demonstrating again that wrapper tests cannot establish an
external tool's contract.

**Decision:** `data-build ncit-store` revalidates the D46 pair before creating anything,
derives a random owner-marked candidate as a sibling of the configured active store, and
bulk-loads both RDF/XML files with the digest-pinned Oxigraph 0.5.3 image. Inferred goes
to the default graph and stated to `STATED_GRAPH_IRI`; `--non-atomic` is allowed only
because this is a private candidate, while `--lenient` is forbidden. The active store
path is never passed to the loader and no rename or activation operation exists in this
workflow.

The candidate is temporarily served on a random loopback port and must prove, with
one-attempt invariant queries: matching release versions; exactly one named graph; bounded
default/stated counts; a production-shaped `C6135` restriction; bounded restriction
population; and the same-release stated-only `C14806 owl:deprecated true` differential.
The persisted manifest binds artifact hashes/pair identity, loader image ID/digest/CLI,
graph layout, exact observations, owner and paths. Its stable source identity excludes
owner and paths so independently built copies of identical inputs compare equal.
Validation container teardown requires independent label, mount, container-ID and file
owner markers. A failed candidate remains inactive with an explicit rejection marker
rather than being mistaken for an activatable store.

Real CLI, double-fidelity, malformed-candidate, and complete pinned-build contracts are
mandatory. Serving activation remains #148 and must consume this proof rather than
re-deriving it.

**Why:** store construction becomes reproducible and directly query-tested without
placing a multi-gigabyte ontology on Graph Store HTTP or giving the builder any way to
replace production.

## 2026-07-29 — NCIt release inputs are one locally certified pair

### D46. Bind stated and inferred artifacts before any offline store construction

EVS publishes the asserted and materialized NCIt variants as separate archives with the
same ontology IRI and release version but different member names and bytes. The old
downloader normalized both extracted files to `Thesaurus.owl`, so a sequential download
overwrote the first artifact. It retained HTTP cache metadata but no content hashes,
ontology identity, or same-release proof. The refresh API and a library helper could then
replace default/protected source graphs with a full ontology over Graph Store HTTP, a path
already known to OOM a memory-limited Oxigraph container (D12).

**Decision:** download the pair to variant-specific archive and OWL paths, stream extraction
and SHA-256 calculation, require each archive's exact regular OWL member, parse the ontology
IRI and `owl:versionInfo`, and publish a deterministic pair manifest only when both variants
match. Persist source URL, HTTP validators, sizes, hashes, ontology identity, variant, and
artifact/pair identities. Every consumer must revalidate the manifest and its files; missing,
modified, swapped, ambiguous, stale-manifest, or cross-release inputs fail closed.

`POST /api/v1/refresh/ncit/download` is pair-download/certification only and rejects legacy
variant/load fields. Generic NCIt reload returns 410, and the former library HTTP loaders
were deleted outright rather than left as raising shims. Offline validated sibling-store
construction belongs to
#181; activation remains #148. Small file-backed generated-graph publication streams from
disk and stays confined to its additive named graph.

**Why:** the pair manifest makes the exact decomposition/query inputs reproducible and
prevents a plausible but mixed release from reaching a mutation boundary. Removing online
source replacement also turns D12's operational workaround into an enforceable invariant.

## 2026-07-26 — caDSR SQLite publishes only from a locally identified release snapshot

### D45. Build privately, certify the standalone candidate, then atomically replace caDSR

The caDSR build extracted a mutable ZIP into a persistent directory and recursively built
from every XML file there. A changed release could therefore include stale files from a
previous release, `ZipFile.extractall` accepted platform-dependent traversal names, and a
failed build had no artifact-level proof before replacing the serving SQLite database.
The upstream endpoint provides HTTP validators but no authoritative checksum or manifest.

**Decision:** under one per-corpus generation lock, copy the cached ZIP into a fresh
private workspace, reject every member unless it is a flat, regular
`cde_xml_<timestamp>_<sequence>.xml` in one contiguous sequence, and pass one sealed bundle
of that provenance and those exact paths to the builder. Reused cached bytes require a
valid persisted download manifest. Preserve available HTTP metadata and compute local
SHA-256 hashes for the archive and ordered member names; the archive hash is reproducibility
provenance, not an authenticity claim. Every XML member must contribute at least one usable
CDE, and any record parser exception rejects the candidate.

The candidate embeds that provenance in `cadsr_source`, uses standalone SQLite `DELETE`
journal mode, and must pass integrity, foreign-key and schema/index-definition, source,
row/JSON invariant, final unique-row-count consistency, and FTS external-content checks.
Successful build returns a sealed validated-candidate token; only that token reaches the
atomic rename callback. The count proves internal build/artifact consistency, not upstream
release completeness: caDSR publishes no authoritative expected count, and the current
81,209-row release must not be rejected by the older 79,827-row embedding expectation.

Existing destination journal/WAL sidecars fail before download or embedding deactivation.
Any failure before the atomic rename preserves the last accepted database and attempts to
remove private candidate artifacts; cleanup failures remain explicit notes on the original
error. A rename failure restores the prior active embedding manifest; an ambiguous
deactivation commit invalidates that connection and reconciles the prior state on a fresh
connection before the original error is reported. Recovery failures likewise remain notes
rather than being mistaken for successful restoration. A successful rename is the commit
point: subsequent ordinary advisory-lock, connection, engine, or candidate-cleanup errors
are logged as committed cleanup incidents rather than reported as a failed publication;
operator cancellation still propagates after cancellation-safe cleanup. Successful source
replacement deliberately leaves caDSR embeddings inactive until their separately validated
D42 rebuild is published.

**Why:** a candidate and its locally verifiable identity become one self-contained
artifact. Atomic replacement gives new repository connections either the old database or
the fully checked new one, while pre-commit failures cannot publish a partial or
mixed-release corpus and post-commit cleanup cannot misreport which database is serving.

## 2026-07-26 — raw SPARQL has no proven bounded HTTP executor

### D44. Caller-supplied raw SPARQL is unavailable until store-side bounds are proven

The public raw-query route parsed SELECT/ASK forms and truncated returned bindings, but
the row cap applied only after Oxigraph executed and the backend materialized the full
response. Its HTTP client timeout did not prove server execution stopped and transport
failures could make up to three attempts for the same unbounded query. Oxigraph 0.5.3 exposes
no verified HTTP cancellation token, server deadline, or per-query resource budget that
the application can enforce.

**Decision:** remove the raw `/api/v1/sparql` route, OpenAPI contract, frontend query
page, navigation, helper types, and settings that implied an execution bound. The typed
backend endpoints remain the application query surface, while direct loopback Oxigraph
queries remain an operator-only datastore check. API security tests reject direct use of
generic query methods and low-level transport dependencies from router modules.

Any future re-enable issue must first prove store-side cancellation and resource and
concurrency limits, then specify a structural allow/deny matrix, real parser/executor
differential tests, no retries, and request-ID and log-redaction contracts. An HTTP client
timeout alone is not cancellation.

**Why:** syntactic read-only checks prevent writes but do not make arbitrary graph work
safe. Removing the route is the only fail-closed behavior supported by the deployed
server.

## 2026-07-26 — lossy decomposition output cannot assert exact equivalence

### D43. Equivalence emission is quarantined until the representation proves completeness

The deployed decomposition is a curated projection: it filters axes, chooses
most-specific fillers, and does not preserve the source's complete multi-parent and
grouped definition. Building `owl:equivalentClass` from that view asserted exactness the
current types could not establish, while a default fidelity value of `0.0` falsely meant
"measured and wholly unfaithful" rather than "unavailable."

**Decision:** every current `emit_equivalence=true` path fails before client, provenance,
stdout, or filesystem effects; normal output never contains `owl:equivalentClass`; and
new curated-projection runs record `roundtrip_fidelity=null` while historical numeric
values remain readable. Issue #153 alone may reintroduce successful emission after it
provides the complete proof-bearing representation required by D19/D21.

**Why:** additive constituent navigation is useful without claiming logical identity.
Failing closed preserves that useful view while preventing a lossy projection from
becoming an inference-grade axiom.

## 2026-07-24 — embedding corpora publish from validated staging

### D42. Embedding batch commits are invisible staging; one transaction activates a complete corpus

The previous NCIt and caDSR generators committed every 200-row batch directly into
their serving tables. An interrupted run therefore published a plausible partial
corpus, and non-empty `vector(768)` rows could not prove completeness or provenance.
**Decision:** each independent corpus build has immutable provenance fields binding a
source hash/version, pinned model ID/revision,
dimension, exact source count, code commit, canonical sentinels, and build ID, plus
mutable lifecycle state/timestamps. NCIt uses and rechecks the exact ordered-record
fingerprint plus live ontology version/count; caDSR uses/rechecks a canonical ordered
CDE-row fingerprint. A changed source fails before
activation (the HTTP source is not falsely described as a database snapshot). Batches
commit only to build-scoped staging. Publication validates exact unique-row count and sentinels, then
holds a per-corpus PostgreSQL advisory transaction lock while replacing the stable
serving rows and switching the single completed-active manifest in one transaction.
Readers require a completed active manifest and report absent certification as 503.
Activation blocks similarity readers under an exclusive lock until the stable-table
replacement and clean HNSW rebuild complete; it guarantees atomic switchover/rollback,
not uninterrupted old-corpus availability. Legacy
rows are never auto-certified; the operator first inspects manifests and must pass
`--publish` (optionally `--corpus ncit|cadsr`) from a clean worktree. Failed candidate
evidence is retained unless an explicit same-build restart clears that attempt; failures
never change the last accepted corpus. **Why:** the transaction
preserves the previous stable table on rollback, avoids dynamic table names and
filtered-ANN under-return, safely rebuilds corpus-specific HNSW, and gives NCIt
and caDSR separate failure domains.

## 2026-07-23 — integration tests own every persistent mutation

### D41. Mutating integration tests use nonce-owned disposable services; full-store contracts are explicitly read-only
Integration tests previously wrote configured Postgres databases and Oxigraph graphs.
Fixed database/graph names and prefix cleanup did not prove ownership and could damage a
developer store or another concurrent run. **Decision:** every persistent mutator is
declared in `test_support/integration_mutators.toml`, marked `mutating_integration`, and
receives a random current-run database and/or a pinned disposable Oxigraph process. The
Postgres database is hosted in its own pinned, loopback-only disposable container and
uses a nonce-owned restricted role; Oxigraph likewise runs in a pinned, loopback-only
container with a nonce-owned data directory. Teardown records immutable container IDs
and refuses destructive cleanup unless independent owner labels, database catalog/schema
markers, and filesystem markers match. Cleanup never uses a prefix wildcard. The safe
runner poisons normal application endpoints before pytest starts, CI provisions no
serving service, collected-item validation enforces marker/fixture ownership per test,
and a static behavioral scanner provides an additional defense-in-depth signal.

`pdm run test-integration` is consequently safe-by-default and excludes `full_store`.
Seeded behavior contracts run against the same bounded disposable Oxigraph fixture.
Contracts whose purpose is to interrogate the configured real corpus are separately
marked `full_store`, are read-only, and run only through
`pdm run test-integration-full-store`. Environment absence is a failure for the default
disposable suite; a skipped full-store data-shape contract is still not a pass when that
contract is an applicable pre-merge gate. `pdm run test --all` also excludes
`full_store`; it is never an implicit real-corpus run. **Why:** a test name, endpoint,
database prefix, or cleanup convention is not an ownership boundary; exact current-run
identity is.

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

**2. Historical proposal, superseded by D60: "NCIt as a subset of Uberon/SNOMED/ICD-O-3."**
NCIt is not a subset: it holds concepts with no upstream counterpart and its class structure differs.
D24–D26 proposed an additive mapping model. D60 now governs ownership: everything emitted is NCIt,
derived from and aligned to corroborating terminology records. The historical addition here was the licence
boundary, which is a hard constraint on what can be *built*, not a legal footnote: **Uberon / CL /
Mondo are open and may be depended on definitionally; SNOMED CT and ICD-O-3 are licence-gated and may
only be mapped to.** An NCIt whose definitions depend on SNOMED cannot be redistributed — which would
defeat the purpose of building it. (#80 stays blocked on a written licence determination, D29.)

> **Current-status correction (2026-08-06) — the licence boundary above is drawn too coarsely, and
> both halves of the "may be depended on definitionally / may only be mapped to" split are wrong.**
>
> **(a) Licence is not the discriminator.** Everything in UMLS / the NCI Metathesaurus is usable.
> NCI holds a licence to use SNOMED CT through NCIm, and the open NCIt Thesaurus already publishes
> 122,853 `P207` UMLS CUIs and 1,252 `P334` ICD-O-3 codes. Ranking external sources by licence
> exposure compares them on the wrong axis.
>
> **(b) Dependency is the thing to avoid — for *every* external source, open ones included.** The
> governing principle is: *align, do not depend; learn, do not copy; corroborate, do not inherit.*
> Take advantage of deep expert curation where it is the best available option and align as far as
> possible, but do not make NCIt's definitions require an external artifact to resolve, do not
> inherit an external source's limitations, and do not import its errors. An NCIt that cannot be
> resolved without Uberon is a dependent ontology whether or not Uberon is CC-BY. So the phrase
> "Uberon / CL / Mondo … may be depended on definitionally" is **withdrawn**.
>
> **(c) The benefit is bidirectional.** NCIt is *designed* to be compatible with these
> terminologies, so alignment strengthens both sides. Where NCIt is right and an external source is
> wrong, alignment work must surface that rather than silently defer outward. Both directions are
> already evidenced: Uberon corrected a wrong NCIt-side comment (`C12470` is Skin, not Lip), while
> NCIt asserts `Malignant Epithelial Cell` on `C9118 Sarcoma`, which is mesenchymal.
>
> **(d) What remains genuinely gated** is narrow: *acquiring and mapping* is unblocked; *publicly
> serving* SCTIDs / ICD-O-3 codes to unlicensed consumers stays entitlement-gated per D29.3; and
> *redistributing a derived ontology whose definitions depend on WHO/IHTSDO content* is the one
> open question for NCI counsel — and under (b) we should not be in that position anyway, because
> external codes are carried as **alignment annotations, not as definitional fillers**. **#80
> re-scopes accordingly.** Note also that ICD-O-3 is not a UMLS source vocabulary (195 SABs in
> 2026AA, none is ICD-O), so ICD-O-3 content must come directly from WHO rather than through the
> NCIm pivot D29/§4.1 assume.
>
> **(e) A relation we need is not "owned" by whoever asserts it first.** `develops_from`
> (`RO:0002202`, ~1,994 uses in Uberon alone) is a shared OBO primitive. Where NCIt lacks a
> relation the model requires, OntoPrism asserts it in its own additive graph under normal review
> and versioning, corroborated by external sources and proposable back to NCI — that is modelling,
> not dependency.



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
**Current-status note (D43):** `roundtrip_fidelity` below describes the intended future
completeness metric. The complete representation does not yet exist; new runs record
`null`, and #153 owns its implementation.

Design §10 asks for a `residual_precoordination` metric and defines it only as "candidates left with an
unresolved multi-aspect label after roles+NLP" — a description, not an operational rule, which is why
`run.py` has carried a "not implemented yet" note rather than a wrong implementation. Two readings were
on the table.

**Decision: a concept is residually pre-coordinated iff it decomposed (produced at least one
constituent) and at least one emitted constituent is *itself* classified as pre-coordinated by the same
detector.** It is reported as a fraction of decomposed concepts.

**Why this and not the label-coverage reading** ("the constituents do not account for every aspect the
label expresses"):
- The label-coverage question is intended to be answered by `roundtrip_fidelity` (D21.3): the
  fraction of *stated OWL restrictions* covered by the emitted equivalence axiom. That is the same
  question asked of the source's own axioms rather than of NLP-extracted "aspects".
- A metric built on NLP aspect extraction moves when the NLP model changes. It would measure our NLP,
  not our ontology — and a quality metric that drifts with an unrelated component is worse than none.
- Once #153 implements it, the two metrics bracket the goal independently: `roundtrip_fidelity` asks **"did we
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
found the first cut of D24–D26 over-claimed in three load-bearing ways; D27–D29 corrected them at the
time. D60 later superseded D24's dual-canonical ownership model: OntoPrism emits provisional NCIt
content, with derivation recorded as provenance and alignment. D27's enumerate-then-measure guarantee
remains current.

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
1. **Lifecycle + drift.** Candidate rows are `proposed`; accepted promotion rows are `validated`; stale
   rows are `quarantined` in a new immutable generation. `active` is an allowed row lifecycle, while the
   active published generation is selected separately. An
   endpoint version bump **re-runs validation** over the affected set (computable from SSSOM version
   fields) and quarantines stale mappings — it does not merely "fail loudly." Mapping reads may surface
   non-validated rows with lifecycle tags, while trusted anchors and identity-grade coverage exclude
   them. Translating an aligned expression into NCIt must return the
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

## 2026-07-11 — historical strategy shift, superseded by D60

Full design-of-record: [`docs/design/ncit-alignment-integration.md`](design/ncit-alignment-integration.md).
Origin: external feedback (a local input memo) recommending an OBO Foundry + SNOMED/ICD-O-3 +
Mondo composite architecture for a next-generation NCIt.

> **Note (2026-07-11, post-review):** D24–D26 below are *hardened by D27–D29 above* following a
> peer-reviewed literature pass and an adversarial red-team. Read them as historical context: the
> caDSR guarantee is enumerate-then-measure (D27), validation is non-circular and reasoner-committed
> (D28), and mapping lifecycle/economics/licensing are corrected (D29). **D60 supersedes D24's
> ownership framing:** all emitted content is proposed NCIt, derived from and aligned
> to corroborating terminologies rather than authored in an upstream plane.

### D24. Historical additive-alignment proposal (ownership superseded by D60)
The feedback's correct intent (be compliant with, and build on, the vetted upstream ontologies —
Uberon anatomy, Cell Ontology normal cells, SNOMED CT + ICD-O-3 morphology, Mondo/DO disease) is
adopted. Its literal prescription — *extract NCIt's anatomy/cell axes and replace them with upstream
IRIs* — is **rejected** because it violates the project's load-bearing invariant (additive, never
mutate the stated OWL; D4/D19) and would break both backward compatibility and caDSR CDE anchoring.

**Historical decision, superseded by D60:** NCIt was described as keeping oncology-specific content
while deferring general scaffolding to mapped reference ontologies. The former dual-canonical model was:
- **Reference plane = NCIt (canonical-of-record for everything that exists today)** — un-mutated,
  backward-compatible, the anchor caDSR CDEs point at.
- **Canonical plane = the upstream stack (then proposed as canonical for new authoring + interop)**.
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
candidate mappings already existed in Mondo and Uberon/CL xrefs and NCIm could supply SNOMED
candidates. NCIm does not supply an ICD-O crosswalk; current ICD-O-3.2 alignments derive from
certified NCIt P334 assertions (D73). caDSR remains untouched, but reach is never assumed transitive:
D27 enumerates its NCIt anchors and measures identity-grade alignment coverage.

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

**Current-status correction (D82; superseding D57/D58 lifecycle guidance):** the original
`MINT-3a7f2c8e901d` identifier and
parent `C12917` were authored outside the deterministic mint contract and before the
source-bound duplicate audit found `C54110 Malignant Germ Cell`. The proposal is now
`MINT-781c8c8c6096` with parent `C54110`. Its sole current strict governance record is
`ontolib/tests/decomposition/golden/proposal-registry.json`, where its state is
`locally-approved`: local SME approval only, not submission, NCI acceptance, runtime publication,
or full-corpus publication. See D82. The old ID, parent, and lifecycle statement below are retained
only as superseded decision history.

> **Current-status correction (2026-08-06) — the organ principle stands; its hand-maintained
> implementation does not, and must not be rebuilt by hand.**
>
> Part 1 below specifies organ codes against *label-level* morphology contexts. It contains **no
> morphology codes**. The code-level table that grew from it —
> `ontolib/src/ontolib/decomposition/site_resolution.py::MORPHOLOGY_TO_ORGAN` — was keyed by
> morphology codes that were never SME-validated, and an audit against the stated OWL on
> 2026-08-06 found **25 of its 32 keys do not denote the concept their comment names**. Examples:
> `C4912` is Bladder Carcinoma (commented "Thyroid Gland Papillary Carcinoma"), `C2851` is
> Acquired Immunodeficiency Syndrome (commented "Gastric Adenocarcinoma"), `C4008` is Recurrent
> Gallbladder Carcinoma (commented "Uterine Carcinosarcoma"). The covering tests were tautologies
> asserting the dict contains what the dict contains, so no test could detect a wrong code.
>
> **Decision: do not rebuild that table by hand.** Anatomy is to be grounded in Uberon through the
> existing xref/promotion layer (`ontolib/src/ontolib/repositories/xref/`), which is built,
> reasoner-corroborated, and has never been run. **20 of the table's 22 organ codes already carry
> a correct `oboInOwl:hasDbXref "NCIT:C…"` authored by Uberon's own editors**; Uberon independently
> corroborates the audit (it gives `C12470 → UBERON:0002097 "skin of body"`, against a comment
> reading "Lip"). The 2 misses — `C19184 Colon,Rectum`, `C203674 Esophagus and GEJ` — are NCIt
> composite staging sites with no anatomical counterpart and are the correct
> `no-upstream-equivalent` residue, to remain hand-curated and explicitly labelled as such.
>
> Sequencing and the per-axis plan are in `docs/design/ncit-alignment-integration.md` §5/§9 and its
> 2026-08-06 status correction.

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
- `op:ClinicalFinding` (R108) — defining `Disease_Has_Finding`
- `op:CellOrigin` (R104) — defining normal cell origin
- R89 and R111–R116 — probabilistic `May_Have_*`, not defining

**Part 3: Settings model change**

| Setting | Old | New | SME Note |
|---------|-----|-----|----------|
| `drop_out_of_scope` | yes | **SPLIT per role** | R100–R108/R110 keep; R89/R111–R116 drop |
| `include_associated_sites` | yes | **maybe** | Metastatic sites need separate axis |

**Part 4: Collisions = Both**

Same anatomy code legitimately appears on multiple axes (primary + associated). No cross-axis deduplication.

**Part 5: Minted concepts for NCIt gaps**

**Historical statement, superseded by D82:** SME identified that C27787 (testicular NSGCT) has no
suitable NCIt cell type and selected the temporary ID `MINT-3a7f2c8e901d` for “Malignant
Non-Seminomatous Germ Cell” with parent C12917. That old ID and parent are not current. D82 points
to `ontolib/tests/decomposition/golden/proposal-registry.json` for the deterministic replacement,
parent, and current local lifecycle state.

**Part 6: Structural bugs identified**

1. **C8515** — AJCC v6 concepts missing R88 fillers; walker/q uery gap vs v7/v8
2. **C208097** — SME preferred C19184 (Colon, Rectum) not in walker's R101 candidates; clinical staging convention overrides OWL-stated narrower code

**Why this matters:**
- Replaces per-cancer lookup table with a single principled rule (D22's univocal relation)
- Validates the entire `op:` namespace proposal (D17/D20 era)
- Establishes minted-concept workflow for NCIt gaps (reproducible, git-tracked)
- Flags two bugs blocking golden set completion

**Evidence (historical):** the original SME review and this decision log preserve what D23 decided.
For current proposal governance and the augmented expected constituent, use the strict registry and
oracle named by D82; do not infer current state from this historical paragraph.

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

**Current-status correction (D59):** source-bound M1 scoring supersedes decision item
2's engine-flag exclusion. Engine `needs_review` flags are diagnostics and do not defer
scoring; D59's strict denominator governs. The current strict view is 80/106 precision
(0.7547) and 80/153 recall (0.5229). The former D21-style exclusion view, retained for
reference only, is 66/85 precision (0.7765) and 66/139 recall (0.4748). Exclusion buys
only +0.0218 precision while costing 0.0481 recall, so D21's "unreachable by
construction" rationale does not survive measurement. The #44 ≥0.9 gate remains
binding and unmet under both views; it is not retired, rescoped, or re-baselined.

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
   nested pair is misread as co-equal rather than collapsed. The current curated projection
   may therefore over-report. D19 requires #153's future complete record to preserve these
   uncertain pairs as separate relationship-group members so nothing is dropped. This is
   why residual ties persist after D20 rather than being a bug to engineer away.
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

**Current-status note (D43):** this entry commits the target architecture, not deployed
behavior. The complete representation and fidelity measurement are deferred to #153;
the current projection is not derived from that still-unbuilt record, and the reserved
emission flag always fails closed.
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
   definition) and is what future `roundtrip_fidelity` (§10) will be measured against.
2. **Adopt SNOMED CT relationship groups as the target axis model.** Where an axis
   legitimately carries several non-nested values, represent them as grouped
   attribute-value sets rather than forcing one (loses information) or flattening
   everything into an undifferentiated bag (stops being a decomposition). This is the
   principled answer §6.5 identified, and it is what lets the co-equal site/lineage and
   region/organ facts coexist without either being dropped.
3. **The single-most-specific, allowlist-filtered output stays the near-term deliverable —
   explicitly flagged as a lossy curated projection**, not the source of truth. Once #153
   builds the complete representation, the projection must be derived and traceable from
   it. The current view is not expected to round-trip and must not be relied on for
   reversibility.
4. **`owl:equivalentClass` emission is the future seam that materializes the
   record-of-truth layer.** D43 assigns successful emission to #153 after it builds the
   proof-bearing representation. Issue #6 owns only the user-facing grammar. Fidelity
   must be validated against a D21-compliant oracle, never inferred `rdfs:subClassOf+`.

**Why not build the full lossless+groups layer now:** the near-term deliverable (neoplasm
5a/5b) needs a curator-readable projection to make progress against the golden set, and the
relationship-groups model is only validated on a handful of concepts (§6.6). Committing the
architecture now — and forbidding the lossy collapse of non-nested values — prevents the
single-valued path from hardening into an irreversible design, while deferring the
complete layer to #153. Full rationale:
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
`R101` triples), consistent with D17's additive principle and D19's groups model. The
curated projection can surface a single primary site and serialize supplied groups, but it
is not the complete record. Issue #153 must preserve every asserted site relationship in
the future record-of-truth layer. Validate via the same golden-set precision/recall
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
streamed as an async byte iterator (chunked).* *Superseded operationally by D46/D47: the manual recipe was removed from `docs/DATA_SETUP.md` because loading into the active store directory is no longer permitted — build a certified inactive sibling with `pdm run data-build ncit-store` instead.*

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
