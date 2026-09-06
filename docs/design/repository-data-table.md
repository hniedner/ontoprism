# Server-owned repository grids

Repository routes own query, pagination, sort, and filter state. The reusable
`DataTable` is a presentation boundary: it renders rows in the order received and
emits typed sort/filter/reset intents. It must not derive repository pages, facet
options, filtered rows, or sorted rows in the browser.

## State contract

- Canonical offset URLs use `q`, `size`, `offset`, `sort`, and repeated categorical
  filter parameters. Page sizes are `10`, `25`, `50`, and `100`, with `25` omitted as
  the default. Offsets are non-negative multiples of the selected size.
- Canonical cursor URLs preserve an ordered trail of opaque `cursor` values. The last
  value is submitted to ClinicalTrials.gov; removing it navigates to the previous
  cursor. Cursor controls never claim a page number, offset, or last page.
- Query, sort, filter, and size changes remove the current offset or cursor trail.
- Malformed browser state redirects once to its canonical representation. Backend
  request models independently reject malformed API input with a 4xx response.
- An offset response must echo the requested `limit`, `offset`, and sort. ClinicalTrials.gov
  responses additionally echo the canonical deduplicated status and phase selections plus
  cursor metadata. Each study preserves its phases as a closed `CTPhase` collection; only
  presentation joins multiple values into readable text. A mismatch is a remote/server contract
  failure, not an empty result.

## Source capabilities

| Source | Pagination | Sorts | Filters |
| --- | --- | --- | --- |
| NCIt | offset, exact total | relevance for search; source order for browse; code and label | representation status |
| caDSR | offset, exact total | source/public ID and name | none |
| ICD-O | offset, exact total | source/code and preferred term | level and morphology behaviour |
| Uberon/CL | offset, exact total | relevance for search; source order for browse; code and label | ontology source |
| PubMed | `retstart`, navigable through NCBI's 10,000-result window | relevance; publication date descending | none |
| ClinicalTrials.gov | opaque cursor trail | remote-service relevance order only | overall status and phase |

Categorical values are closed source domains rather than values sampled from the
loaded page. Repeated values are OR-combined within a column; filters in different
columns and the search query are AND-combined. Selected values remain in canonical
state even when a page contains no matching example. These closed filters do not
display inferred per-option counts.

Local PostgreSQL/SQLite ordering must be total and null-last. Every configurable
order ends in the repository's immutable identifier. Certified NCIt and Uberon search
indexes are authoritative for search: an absent, stale, or unavailable index fails
closed instead of changing page semantics through a QLever fallback.

NCIt and Uberon browse responses have browse-only sort types, distinct from their
search response types. caDSR FTS pagination obtains an exact total independently of
the requested result window, so an offset beyond the final hit still carries the
authoritative nonzero total.

## Presentation and failures

The table exposes semantic headers, `aria-sort`, labelled filter controls, an active
sort label, removable active-filter chips, reset actions, a keyboard-focusable
horizontal-scroll region, `aria-busy`, and a polite row-count announcement. Cells are
compiled typed snippets; trusted HTML is not part of the contract. Missing or duplicate
row keys fail closed before rows render. Revalidation keeps the current table available,
marks that table busy, and adds a delayed loading announcement.

Routes distinguish an initial search instruction, an empty repository, no matches,
revalidation, rate limiting, timeout, unavailability, and malformed source data.
Successful empty pages retain the same table, columns, sort/filter controls, active
chips, reset actions, and meaningful previous-page or cursor recovery as populated pages.
Navigation state is URL-persisted so SvelteKit can discard superseded navigation
results rather than allowing stale responses to replace newer state.

## Verification

Focused deterministic contracts use `pdm run agent-test <node> -v` and
`pdm run agent-test --frontend <tracked-test-file>`. Disposable PostgreSQL/QLever
contracts use `pdm run agent-test --safe-integration <node> -v`. Configured corpus
contracts use `pdm run agent-test --full-store <node> -v`. The complete acceptance gate
is `pdm run verify`.

On 2026-09-06, the successful-empty table and recovery contracts passed
(`pdm run agent-test --frontend frontend/src/lib/components/RepoBrowsePage.svelte.test.ts frontend/src/routes/repositories/uberon/page.svelte.test.ts frontend/src/routes/repositories/pubmed/page.svelte.test.ts frontend/src/routes/repositories/clinicaltrials/page.svelte.test.ts`, exit 0), the caDSR past-end exact-total contract passed
(`pdm run agent-test ontolib/tests/repositories/test_cadsr_fts.py -v`, 7 passed), and the
live two-status/two-phase ClinicalTrials.gov contract passed
(`pdm run agent-test --full-store ontolib/tests/repositories/test_clinicaltrials_client.py::test_live_clinicaltrials_multi_filter_domain_and_union -v`, 1 passed).
