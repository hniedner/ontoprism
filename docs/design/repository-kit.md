# Repository kit for the six shipped repositories (#487; approved design)

This is reuse **inside the six shipped repositories**, not the ontology-generic
platform. Existing `repository-manifest.json`, `backend/repository_registry.py`,
`frontend/src/lib/repository-registry.ts`, `backend/api/v1/grid.py`,
`frontend/src/lib/server/repository-load.ts`, `RepoBrowsePage`, `DataTable`,
`RepoSearchBar`, `RepoPageHeader`, `RemoteSearchSurface`, and `AlignmentLinks`
were inspected before proposing any new code. The owner approved this design
with amendments in [#487](https://github.com/hniedner/ontoprism/issues/487#issuecomment-5897027546).
It authorizes neither a schema migration, a new table, nor a new source release.

## Current inventory (code as of M10)

| Repository | List/search/detail and metadata | Filters and sorts | Graph and cross-references |
| --- | --- | --- | --- |
| NCIt | `backend/api/v1/ncit.py`: QLever list, certified Postgres FTS search, QLever concept detail; `repository_metadata.py` readiness | `representation_status`; code/label, plus relevance and source order (`ncit/search_index.py`) | NCIt neighborhood, roles and decomposition; `concept_xref` mappings, separate caDSR CDE lookup (`cadsr.py`) |
| Uberon/CL | `backend/api/v1/uberon.py`: QLever list/detail, certified Postgres FTS search; same metadata service | `source` (Uberon or CL); code/label, relevance/source order (`uberon/search_index.py`) | Named hierarchy, neighborhood and typed OWL restrictions; `concept_xref` alignments |
| ICD-O | `backend/api/v1/icdo.py`: generation-bound list/search/detail and metadata, for the served edition/axis pairs | Level (morphology only offers `morphology`; topography offers category/leaf); morphology behaviour; code/preferred, source order. Unsupported 3.2 topography is not silently served. | No ontology neighborhood; 4.0 topography congruence view; 3.2 morphology links through `concept_xref` |
| caDSR | `backend/api/v1/cadsr.py` and `ontolib/repositories/cadsr/repository.py`: SQLite list/search/detail; certified archive metadata | No list filter; source/public ID/name sorts | CDE pseudo-node joined to NCIt neighborhoods; direct `cde_concepts` component and value-meaning anchors, **not** `concept_xref` mappings today |
| PubMed | `backend/api/v1/pubmed.py` → `PubMedClient`: upstream POST search, GET detail; remote availability (no local certification metadata) | No filter; relevance or publication date descending; no rows before query; offset within NCBI 10,000-result window | Related-article links, not ontology edges or semantic mappings |
| ClinicalTrials.gov | `backend/api/v1/clinicaltrials.py` → `ClinicalTrialsClient`: upstream POST search, GET detail; remote availability | Status and phase; remote relevance order only; no rows before condition query; opaque cursor trail | Trial publication references, not ontology equivalences |

All six render the **same** `data-table/DataTable.svelte`, header, filter popover
and row/empty treatment, but six small results-table wrappers declare columns
separately. Four local pages use `RepoBrowsePage`; the two remote pages
repeat header/search/results-state glue and supply different pagination.
`repository-manifest.json` presently declares only ID, label, path and kind;
sort/filter maps are duplicated among route loaders, page components, table
wrappers, backend request types and the fixed smoke flow. The two local FTS
modules duplicate publication/count/ordering mechanics with different source
and row shapes. `backend/api/v1/grid.py` shares only the page-size validator.

## Approved contract and ownership

One **closed, version-controlled capability declaration per shipped repository**
extends the existing `repository-manifest.json` (approved version-controlled
configuration, **not** stored data and requiring no database migration).
It holds **capabilities only**: sorts, filters (both kinds and controlled value
domains), pagination kind, query-before-results, metadata kind, graph kind
and link kind. It holds **no columns or rendering hints**. Columns remain
typed snippets in the one shared `DataTable`; this is not a configuration-driven
UI engine. The Python registry boundary rejects unknown keys and contradictory
capabilities; the frontend consumes that checked-in declaration as typed
configuration without a second validator. The API, table and smoke read the
same declaration for supported controls. Smoke still checks actual
rendered results, rather than treating a declaration as evidence that they work.
ICD-O entitlement stays enforced in the backend; declaration is never authorization.

- **Terminology base (NCIt, Uberon/CL, ICD-O, caDSR):** one backend grid
  service in `backend/api/v1/grid.py` orchestrates validated list/search/detail
  parameters, certification before **every local list, search and detail read**,
  closed filters/sorts and typed errors. Store-specific query functions remain in the existing ontolib
  repositories (`NcitGraphStore`, `UberonGraphStore`, `IcdoRepository`,
  `CdeRepository`); do **not** force QLever, SQL and SQLite through an
  inefficient common query language. Source-bound FTS publication mechanics
  converge from the two existing `search_index.py` implementations, preserving
  different rows, source checks, query ranking and atomicity. `RepositoryMetadataService`
  remains the authority for local certification.
- **Ontology extension (NCIt, Uberon/CL):** opt-in hierarchy, neighborhood,
  typed relations and graph in the existing graph-store readers and
  `BrowserGraph`/`GraphExplorer`. A caDSR graph may navigate NCIt through
  source anchors without claiming that caDSR is an OWL ontology. ICD-O need
  not invent an ontology graph.
- **Remote read-through (PubMed, ClinicalTrials.gov):** the existing
  `PubMedClient`/`ClinicalTrialsClient` remain upstream adapters. A shared
  frontend list/detail shell reuses `RepoBrowsePage`, `RemoteSearchSurface`,
  `RepoResultsCard`, `RepoSearchBar`, one `DataTable`, and configurable
  offset/cursor pagination. The backend retains upstream-specific validation
  and rate-limit/timeout/error translation. Remote list remains an instruction
  until a query. Sort/filter are optional **only if absent upstream**; current
  PubMed filter and trial sort are declared unavailable, not labelled working.

One shared **frontend detail layout** hosts typed snippets (concept,
article, study, data element) rather than six copy/pasted navigation and
error-state implementations. One results table receives typed column and cell
snippets (not manifest rendering configuration); remove the six wrapper
components when equivalent functionality is wired. Search, sort, multi-select
and type-ahead filters, paging, zero-result, error state and detail-link controls
keep the same accessible labels, focus behavior and navigation semantics. The one
`RepoBrowsePage` owns URL state, and the already shared
`repository-load.ts` owns canonical offset/cursor parsing; no second state
owner. Fields absent upstream remain explicit in the UI, not synthesized.

**Approved filter behavior:** every filterable column in every repository has
type-ahead text in the filter popover. A closed-domain column **also** has a
multi-select; typing narrows its option list and may be applied as a text
predicate on the column. Both applied predicates must match. One shared
`DataTable` supports both kinds together; one shared backend predicate
applies declared filters before pagination and computes the filtered total.
NCIt retains status, Uberon/CL source and ICD-O morphology behaviour/topography
level; the single-valued morphology level control adds no narrowing.
caDSR gets a multi-select on **every** closed-domain field, not just one:

| caDSR source field | Distinct values on configured store (79,835 CDEs; owner observation) |
| --- | ---: |
| `value_domain_type` | 3 |
| `workflow_status` | 5 |
| `registration_status` | 10 |
| `context` | 51 |
| `datatype` | 97 |

Preserve source spellings, including distinct `registration_status` values
“Superceded” (638) and “Superseded” (112). These figures come from the
[owner's read-only observation](https://github.com/hniedner/ontoprism/issues/487#issuecomment-5897005792),
not a new local count. #484 delivers shared behavior and NCIt code/label/status
controls; the other repositories acquire filters in their M12 migrations.

## Cross-repository links: one construct, distinct semantics

Reuse `ontolib/repositories/xref/` (`SSSOMRecord`,
`XrefStore`, generation publication), `backend/api/v1/alignment.py` and
`AlignmentLinks`. **One typed API read projection and one frontend component**
serve both variants. The envelope records endpoint kind, identifier/version,
direction, relation kind and provenance/status. The
**semantic mapping** variant uses existing SSSOM fields and honest SKOS
`exactMatch`/`closeMatch`/`broadMatch`/`narrowMatch`/`relatedMatch` annotations;
no SKOS annotation by itself grants logical equivalence. A **caDSR source
anchor** is a different relation: CDE `(public_id, version)` component or
permissible-value meaning references an NCIt code, preserving `concept_type`
(`object_class`, `property`, `representation`, `value_meaning`) and `is_primary`.
Its reverse lookup is navigation, **not** a SKOS match from NCIt to a CDE.
The existing `concept_xref` schema admits only NCIt/Uberon/ICD-O endpoint
pairs and mapping predicates: **keep that schema unchanged**. caDSR anchors
remain in the caDSR store and are projected at read time into the shared API
link envelope. This is one read construct, not a second copy of the anchors.
No synthetic `skos:exactMatch`, inferred CDE↔NCIt identity, or promotion of
mapping lifecycle to enhanced-NCIt proposal lifecycle. Existing caDSR source
annotations remain the source of truth; an API projection must not invent
unrecorded NCIt-release binding, evidence, lifecycle or approval status.

## Approved bounded migration order (M12 issue contracts created by owner)

Every issue includes its motivating behavioural tests, first RED then GREEN;
estimate is **net non-test lines** after removing duplicates, not a budget to
add parallel implementations. If an issue threatens ~400 net lines, stop and
ask the owner to split it with its tests. The owner creates the migration
issues from this amended design; implementers do not create them.

1. **NCIt reference slice** (first): extend the approved capabilities-only
   registry, reuse existing grid/list/detail controls and #484's shared
   predicate/table, preserve certified search. Move the verified #486
   certification bypass behind **one** shared grid guard: no per-route patch
   and no degraded direct reads. Target net -30 to +120 lines.
2. **Uberon/CL**: declare its two-source filter and sort capabilities and graph
   extension; reuse NCIt grid/publication mechanics, remove duplicate paths.
   Target net -120 to +60 lines.
3. **ICD-O**: inherit the shared grid, preserve backend entitlement and
   edition/axis constraints. Target net -120 to +100 lines.
4. **caDSR**: inherit the shared grid and read-projected link construct,
   add all five approved source-domain multi-selects plus type-ahead without
   altering the caDSR anchors or `concept_xref`. Target net -120 to +100 lines.
5. **Remote read-through pair**: share shell and cursor/offset control while
   retaining separate upstream clients. Target net -160 to +80 for the pair.
   If both remote repositories do not fit one coherent issue under the size cap,
   **stop and ask the owner for a split**; do not silently overfill M12.

M12 has **five migrations and no separate filter issue**. Each repository's
filters are in its migration; #485 is absorbed/closed by the owner when those
issues are created. Under the 2026-10-02 D99 amendment, acceptance requires no
parallel implementation; a handler holds only its typed signature and store
call; shared code is used by at least two repositories; and net size is
reported. Each issue states expected and actual net non-test lines and explains
a positive net.

For each migration, compare measured cold and warm endpoint latency, request
counts, result and page shapes against the same configured dataset before
and after the change (using #486's measurements where comparable), plus a browser flow
for search/filter/sort/paging/empty/error/detail and the read-only
`pdm run smoke-real` gate. Preserve or improve latency and avoid extra
per-row store calls; any slower path needs a profile and correction before
acceptance. No database migration, new table, new source release or source
activation is approved by this design. The manifest-format change is approved
as version-controlled configuration, not stored data.
