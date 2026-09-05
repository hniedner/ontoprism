# Repository data table

## Decision

OntoPrism owns a small native Svelte 5 `DataTable`. Its columns retain compiled
`Snippet<[Row]>` cells for domain links, badges, and compound values, while sort and
filter projections are scalar-only. The component has no HTML-string API, no URL-column
API, no pagination, and no backend or navigation callback (`rg -n "Snippet|DataTableOperations|goto|Pagination|html" frontend/src/lib/components/data-table`).

The `none | client-page` operation union deliberately names the only interactive scope.
`client-page` requires visible copy explaining that operations affect the loaded page;
repository query, offset, total, page size, filters, and navigation remain outside the
table (`rg -n "client-page|scopeLabel|Pagination|goto" frontend/src/lib/components/data-table frontend/src/lib/components/RepoBrowsePage.svelte`).

The behavior was derived from the Apache-2.0 Fairdata Workbench table tests and component
identified in issue #326, then reduced to OntoPrism's contract. No Fairdata source was
copied and no Fairdata or TanStack runtime package was added (`rg -n "tanstack|fairdata"
frontend/package.json frontend/package-lock.json frontend/src || true`).

## Complete table-surface inventory

The inventory was taken from all Svelte table markup before and after extraction (`rg -n
"<table|ResultsTable" frontend/src --glob '*.svelte'`). Repository server loaders establish
the local page size and URL state (`rg -n "PAGE_SIZE|offset|searchParams" frontend/src/routes/repositories --glob '+page.server.ts'`).

| Surface and route | Row/column contract and source | Existing ownership and scale | Decision |
| --- | --- | --- | --- |
| `SearchResultsTable`, `/repositories/ncit` | `SearchHit`; typed concept links, code, nullable label and semantic type, representation badge; local certified API | `RepoBrowsePage` owns `q`, `offset`, total, 25-row pages, loading, empty state, server representation-status filter, and pagination; Name ascending was the table default | **Selected.** Keep the default and add disclosed page-local sort/filter controls. |
| `CdeResultsTable`, `/repositories/cadsr` | `CdeSummary`; typed public-ID links plus version, long/short names, nullable context and datatype; local caDSR proxy | `RepoBrowsePage` owns `q`, `offset`, total, 25-row pages, loading, empty state, and pagination | **Selected.** Preserve compound cells and add disclosed page-local operations. |
| `PubMedResultsTable`, `/repositories/pubmed` | `PubMedArticleSummary`; typed PMID links, title, first three authors, nullable journal/date; live PubMed result subset | Route search state and `RepoResultsCard` own navigation, loading, remote error, count, and source-empty presentation; no table pagination | **Selected.** Preserve service order until user sorting and operate only on the returned subset. |
| `CtResultsTable`, `/repositories/clinicaltrials` | `CTStudySummary`; typed NCT links, title/conditions, nullable status/phase; live ClinicalTrials.gov result subset | Route search state and `RepoResultsCard` own navigation, loading, remote error, count, and source-empty presentation; no table pagination | **Selected.** Preserve service order until user sorting and operate only on the returned subset. |
| `IcdoResultsTable`, `/repositories/icdo/[edition]/[axis]` | The four `IcdoRecord` variants across 3.2 morphology, 4.0 morphology, and 4.0 topography; encoded code link, preferred fallback, level; protected local API | `RepoBrowsePage` owns `q`, `offset`, total, 25-row pages, loading, empty state, server behaviour/level filters, and pagination | **Selected.** Retain all variants and server filter state; page-local controls do not alter URLs. |
| Inline Uberon/CL table, `/repositories/uberon` | `UberonSearchHit`; typed CURIE links, nullable label, normalized source display; local certified combined index | `RepoBrowsePage` owns `q`, `offset`, total, 25-row pages, loading, empty state, source filter URL/reset behavior, and pagination | **Selected and extracted** as `UberonResultsTable`; page-local controls never navigate. |
| Refresh report, `/refresh` | Repository metadata rows with lifecycle status and certified source/manifest fields; one generated refresh report | The refresh workflow owns loading/error/report state and row rendering uses `RepositoryMetadataRow`; no query or pagination | **Deferred.** It is an operational mutation report, not repository browsing; issue #326 explicitly excludes refresh. |
| ICD-O congruence report, `/repositories/icdo/4.0/topography/congruence` | Congruence classifications, reasons, and candidate arrays from the protected report | The route renders the complete report and count summary without browse controls | **Deferred.** It is a specialized inspection report; issue #326 explicitly excludes congruence. |

## Behavioral contract

Sorting is stable and scalar-typed. Strings compare case-insensitively with a fixed
`en-US` normalization, numbers and booleans compare by value, mixed non-null scalar types
are refused, and null remains last in both directions (`pdm run agent-test --frontend
frontend/src/lib/components/data-table/DataTable.svelte.test.ts`). Filters trim both query
and projected values, compare case-insensitively, and combine by AND. Source-empty and
filter-empty messages remain distinct.

Rows and columns are validated before body rows render. Empty or duplicate row IDs,
duplicate column IDs, invalid initial sort/filter metadata, mixed sortable types, and
invalid sticky offsets throw configuration errors that omit row values. Sticky headers
and columns use caller-provided pixel offsets with opaque backgrounds and explicit stacking
(`pdm run agent-test --frontend frontend/src/lib/components/data-table/DataTable.sticky.svelte.test.ts`).

All core text uses normal Svelte interpolation, and rich cells are compiled snippets.
Hostile text sweeps cover core surfaces and every selected wrapper without catching render
failures (`pdm run agent-test --frontend frontend/src/lib/components/data-table/DataTable.xss-sweep.svelte.test.ts frontend/src/lib/components/SearchResultsTable.svelte.test.ts frontend/src/lib/components/CdeResultsTable.svelte.test.ts frontend/src/lib/components/PubMedResultsTable.svelte.test.ts frontend/src/lib/components/CtResultsTable.svelte.test.ts frontend/src/lib/components/IcdoResultsTable.svelte.test.ts frontend/src/lib/components/UberonResultsTable.svelte.test.ts`).
