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

The behavior was derived from the Fairdata Workbench table tests and component identified
in issue #326, then implemented as a clean behavioral adaptation to OntoPrism's contract.
Fairdata's `LICENSE` identifies Apache-2.0 (`cat ../fairdata/LICENSE`, inspected
2026-09-05), as does OntoPrism's `LICENSE` (`cat LICENSE`, inspected 2026-09-05). No
Fairdata source or dependency was copied, and no Fairdata or TanStack runtime package was
added (`rg -n "tanstack|fairdata" frontend/package.json frontend/package-lock.json
frontend/src || true`).

## Complete table-surface inventory

The inventory was taken from all Svelte table markup before and after extraction (`rg -n
"<table|ResultsTable" frontend/src --glob '*.svelte'`). The 25-row local page size is
established by the applicable server loaders and API calls, while `RepoBrowsePage` owns
the matching pagination contract (`rg -n "PAGE_SIZE|limit: 25|limit=\\{25\\}|offset|searchParams"
frontend/src/routes/repositories frontend/src/lib/components/RepoBrowsePage.svelte`).

| Surface and route | Row/column contract and source | Existing ownership and scale | Decision |
| --- | --- | --- | --- |
| `SearchResultsTable`, `/repositories/ncit` | `SearchHit`; typed concept links, code, nullable label and semantic type, representation badge; local certified API | `RepoBrowsePage` owns `q`, `offset`, total, 25-row pages, loading, empty state, server representation-status filter, and pagination; Name ascending was the table default | **Selected.** Keep the default and add disclosed page-local sort/filter controls. |
| `CdeResultsTable`, `/repositories/cadsr` | `CdeSummary`; typed public-ID links plus version, long/short names, nullable context and datatype; local caDSR proxy | `RepoBrowsePage` owns `q`, `offset`, total, 25-row pages, loading, empty state, and pagination | **Selected.** Preserve compound cells and add disclosed page-local operations. |
| `PubMedResultsTable`, `/repositories/pubmed` | `PubMedArticleSummary`; typed PMID links, title, first three authors, nullable journal/date; live PubMed result subset | Route search state and `RepoResultsCard` own navigation, loading, remote error, count, and source-empty presentation; no table pagination | **Selected.** Preserve service order until user sorting and operate only on the returned subset. |
| `CtResultsTable`, `/repositories/clinicaltrials` | `CTStudySummary`; typed NCT links, title/conditions, nullable status/phase; live ClinicalTrials.gov result subset | Route search state and `RepoResultsCard` own navigation, loading, remote error, count, and source-empty presentation; no table pagination | **Selected.** Preserve service order until user sorting and operate only on the returned subset. |
| `IcdoResultsTable`, `/repositories/icdo/[edition]/[axis]` | The four `IcdoRecord` variants across 3.2 morphology, 4.0 morphology, and 4.0 topography; encoded code link, preferred fallback, level; protected local API | `RepoBrowsePage` owns `q`, `offset`, total, 25-row pages, loading, empty state, server behaviour/level filters, and pagination | **Selected.** Retain all variants and server filter state; page-local controls do not alter URLs. |
| Inline Uberon/CL table, `/repositories/uberon` | `UberonSearchHit`; typed CURIE links, nullable label, normalized source display; local certified combined index | `RepoBrowsePage` owns `q`, `offset`, total, 25-row pages, loading, empty state, source filter URL/reset behavior, and pagination | **Selected and extracted** as `UberonResultsTable`; page-local controls never navigate. |
| Refresh report, `/refresh` | Repository metadata rows with lifecycle status and certified source/manifest fields; one generated refresh report | The refresh workflow owns loading/error/report state and row rendering uses `RepositoryMetadataRow`; no query or pagination | **Deferred.** The team kept this operational mutation report outside the repository-browsing table abstraction. |
| ICD-O congruence report, `/repositories/icdo/4.0/topography/congruence` | Congruence classifications, reasons, and candidate arrays from the protected report | The route renders the complete report and count summary without browse controls | **Deferred.** The team kept this specialized inspection report outside the browse-table abstraction. |

## Behavioral contract

Sorting is stable and scalar-typed. Strings compare case-insensitively with a fixed
`en-US` normalization, numbers and booleans compare by value, mixed non-null scalar types
are refused, and null remains last in both directions. Filters are an explicit `text |
categorical` discriminated union; categorical behavior is never inferred from values.
Text filters trim both query and projected values and compare case-insensitively.
Categorical options come only from the currently loaded rows, use the same stable string
ordering, include counts, and OR selected values within a column while all columns remain
AND-combined. Null is a dedicated labelled option, while an empty string is a normal,
separately labelled value distinct from null, the literal `null`, and the displayed dash.
More than 25 distinct non-null values fails with a sanitized table validation error rather
than truncating or changing filter type. Row updates rebuild the option inventory and prune
only unavailable selections; losing every selected value clears that categorical filter
(`pdm run agent-test --frontend frontend/src/lib/components/data-table/DataTable.svelte.test.ts`).
Source-empty and filter-empty messages remain distinct.

The selected adapters use categorical controls only for bounded semantic fields: NCIt
semantic type and representation status; caDSR datatype (context stays text because it can
be high-cardinality); ClinicalTrials.gov status and phase; ICD-O level, behaviour, and
specificity across every dataset variant; and Uberon/CL source. PubMed has no categorical
column because its journal and other metadata are free text. All are page-local over the
existing at-most-25 loaded rows and do not take repository query or pagination ownership
(`rg -n "kind: 'categorical'|scopeLabel" frontend/src/lib/components/{SearchResultsTable,CdeResultsTable,CtResultsTable,IcdoResultsTable,UberonResultsTable,PubMedResultsTable}.svelte`).

Rows and columns are validated before body rows render. Blank captions/region labels,
empty or duplicate column IDs, empty or duplicate row IDs, invalid initial sort/filter
metadata, non-finite numbers, mixed sortable types, and invalid sticky offsets render a
sanitized accessible error instead of stale or invalid rows. Configuration with no visible
operation scope cannot expose sort or filter controls. All six selected adapters enable the
top-zero sticky header and make their identifier/code column sticky left at offset zero;
header and body cells retain opaque backgrounds and explicit stacking. Loading and remote
errors remain owned by `RepoResultsCard` or `RepoBrowsePage`, so `DataTable` accepts ready
rows and owns only source-empty and page-filter-empty presentation
(`pdm run agent-test --frontend frontend/src/lib/components/data-table/DataTable.sticky.svelte.test.ts`).

All core text uses normal Svelte interpolation, and rich cells are compiled snippets.
Hostile text sweeps cover core surfaces and every selected wrapper without catching render
failures (`pdm run agent-test --frontend frontend/src/lib/components/data-table/DataTable.xss-sweep.svelte.test.ts frontend/src/lib/components/SearchResultsTable.svelte.test.ts frontend/src/lib/components/CdeResultsTable.svelte.test.ts frontend/src/lib/components/PubMedResultsTable.svelte.test.ts frontend/src/lib/components/CtResultsTable.svelte.test.ts frontend/src/lib/components/IcdoResultsTable.svelte.test.ts frontend/src/lib/components/UberonResultsTable.svelte.test.ts`).
