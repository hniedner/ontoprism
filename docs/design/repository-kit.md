# Repository kit proposal (#487; awaiting owner approval)

This is reuse **inside the six shipped repositories**, not the ontology-generic
platform. Existing `repository-manifest.json`, `backend/repository_registry.py`,
`frontend/src/lib/repository-registry.ts`, `backend/api/v1/grid.py`,
`frontend/src/lib/server/repository-load.ts`, `RepoBrowsePage`, `DataTable`,
`RepoSearchBar`, `RepoPageHeader`, `RemoteSearchSurface`, and `AlignmentLinks`
were inspected before proposing any new code. No production change, schema, or
migration is authorized by this proposal.

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

## Proposed contract and ownership

One **closed, version-controlled capability declaration per shipped repository**
extends the existing `repository-manifest.json` (a persisted format change;
requires explicit approval before implementation). Include supported dataset
variants, columns (IDs/labels/rendering hints), sort keys and default sort by
mode, filter keys/allowed values or bounded text-search capability, pagination
kind, query-before-results, metadata/readiness kind, graph/link capabilities,
and path templates. The Python and TypeScript validators at the existing
registry boundaries reject unknown keys, contradictory sorts/filters, and
unserved variants. Both API validation and UI tables read the same declaration;
neither silently invents a capability. Smoke uses the declaration for the
supported control *list* while checking actual rendered results, including
explicitly unsupported controls, rather than treating a capability declaration
as proof that a control works. The approval must also specify where any
security-sensitive capability (for example ICD-O entitlement) is enforced;
frontend declaration is not authorization.

- **Terminology base (NCIt, Uberon/CL, ICD-O, caDSR):** one backend grid
  service in `backend/api/v1/grid.py` orchestrates validated list/search/detail
  parameters, certification before dependent reads, closed filters/sorts and
  typed errors. Store-specific query functions remain in the existing ontolib
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

One configurable **frontend detail layout** hosts typed snippets (concept,
article, study, data element) rather than six copy/pasted navigation and
error-state implementations. One results table receives declarative column
specifications and existing typed cell snippets; remove the six wrapper
components when equivalent functionality is wired. Search, sort, multi-select
filter, paging, zero-result, error state and detail-link controls keep the same
accessible labels, focus behavior and navigation semantics. The one
`RepoBrowsePage` owns URL state, and the already shared
`repository-load.ts` owns canonical offset/cursor parsing; no second state
owner. Fields absent upstream remain explicit in the UI, not synthesized.

**Filter decision proposed for approval:** terminology base requires at least
one meaningful filter if the source exposes an appropriate field. NCIt retains
status, Uberon/CL source, ICD-O morphology behaviour/topography level; omit
the single-valued morphology level control because it cannot narrow results.
For caDSR, propose certified closed domains `workflow_status` and
`registration_status` (inspect actual source values, including nulls and
cardinality, before selecting which to expose). #484 adds shared NCIt text
and categorical filtering through one `DataTable` and one backend predicate,
with the filter declaration in configuration; it must not preempt the owner's
decision about caDSR domains or make a second repository-specific table.

## Cross-repository links: one construct, distinct semantics

Reuse `ontolib/repositories/xref/` (`SSSOMRecord`,
`XrefStore`, generation publication), `backend/api/v1/alignment.py` and
`AlignmentLinks`. The single typed link envelope records endpoint kind,
identifier/version, direction, relation kind and provenance/status. The
**semantic mapping** variant uses existing SSSOM fields and honest SKOS
`exactMatch`/`closeMatch`/`broadMatch`/`narrowMatch`/`relatedMatch` annotations;
no SKOS annotation by itself grants logical equivalence. A **caDSR source
anchor** is a different relation: CDE `(public_id, version)` component or
permissible-value meaning references an NCIt code, preserving `concept_type`
(`object_class`, `property`, `representation`, `value_meaning`) and `is_primary`.
Its reverse lookup is navigation, **not** a SKOS match from NCIt to a CDE.
The existing `concept_xref` schema admits only NCIt/Uberon/ICD-O endpoint
pairs and mapping predicates; the owner must approve a schema change or an
alternative unified read projection **before** any persistence implementation.
No synthetic `skos:exactMatch`, inferred CDE↔NCIt identity, or promotion of
mapping lifecycle to enhanced-NCIt proposal lifecycle. Existing caDSR source
annotations remain available during transition; decide their provenance,
NCIt-release binding and status with the owner before migration.

## Proposed bounded migration order (owner decides placement)

Every issue includes its motivating behavioural tests, first RED then GREEN;
estimate is **net non-test lines** after removing duplicates, not a budget to
add parallel implementations. If an issue threatens ~400 net lines, split
it with its tests before work. No migrations are created before approval.

1. **NCIt reference slice** (first): extend the approved registry shape and
   existing grid/list/detail controls; one shared predicate and table build
   from #484, preserving certified search. Target net -30 to +120 lines.
2. **Uberon/CL**: declare its two-source column/filter rules and graph
   extension; reuse NCIt grid/publication mechanics, remove duplicate paths.
   Target net -120 to +60 lines.
3. **ICD-O and caDSR local variants**: migrate one at a time if the per-issue
   cap allows; preserve licensed access and CDE anchors, introduce only
   owner-approved caDSR filters. Target net -120 to +100 per repository.
4. **Remote read-through pair**: share shell and cursor/offset control while
   retaining separate upstream clients; split PubMed and trials if the
   combined issue cannot fit. Target net -160 to +80 for the pair.

This is **five or six possible migration issues**, not a proposal to overfill
M12 (which reserves #485 and at most four migrations). Owner selects which
four fit M12, and where remaining work belongs, before issues are created;
do not silently batch independent repository migrations to meet the slot cap.

For each migration, compare the current endpoint p50/p95 cold and warm
latencies, request counts, result and page shapes against the same configured
dataset after the change (`#486` establishes baseline), plus a browser flow
for search/filter/sort/paging/empty/error/detail and the read-only
`pdm run smoke-real` gate. Preserve or improve latency and avoid extra
per-row store calls; any slower path needs a profile and correction before
acceptance. A new migration that adds more non-test lines than it removes
needs a concrete rationale in its demo. No production data migration or
source activation is implied by this design.

## Owner decisions requested

1. Approve or amend the single declaration and its persisted manifest-format
   change, including security-sensitive capabilities and the caDSR filter domain.
2. Confirm the link envelope's **mapping vs source-anchor** distinction,
   CDE component role vocabulary, version/currentness and whether the
   `concept_xref` table or a unified read projection is the eventual storage.
3. Confirm the migration order and M12's at-most-four migration slots plus
   #485; place overflow in a later owner-approved milestone. Approval of a
   design alone does **not** approve a schema migration.
