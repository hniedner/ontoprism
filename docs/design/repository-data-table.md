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
- An offset response must echo the requested `limit` and `offset`. Remote responses
  additionally echo their supported sort or cursor metadata. A mismatch is a
  remote/server contract failure, not an empty result.

## Source capabilities

| Source | Pagination | Sorts | Filters |
| --- | --- | --- | --- |
| NCIt | offset, exact total | relevance for search; source order for browse; code and label | representation status |
| caDSR | offset, exact total | source/public ID and name | none |
| ICD-O | offset, exact total | source/code and preferred term | level and morphology behaviour |
| Uberon/CL | offset, exact total | relevance for search; source order for browse; code and label | ontology source |
| PubMed | `retstart`, navigable through NCBI's 10,000-result window | relevance and publication date | none |
| ClinicalTrials.gov | opaque cursor trail | remote-service relevance order only | overall status and phase |

Categorical values are closed source domains rather than values sampled from the
loaded page. Repeated values are OR-combined within a column; filters in different
columns and the search query are AND-combined. Selected values remain in canonical
state even when a page contains no matching example.

Local PostgreSQL/SQLite ordering must be total and null-last. Every configurable
order ends in the repository's immutable identifier. Certified NCIt and Uberon search
indexes are authoritative for search: an absent, stale, or unavailable index fails
closed instead of changing page semantics through a QLever fallback.

## Presentation and failures

The table exposes semantic headers, `aria-sort`, labelled filter controls, an active
sort label, removable active-filter chips, reset actions, a keyboard-focusable
horizontal-scroll region, `aria-busy`, and a polite row-count announcement. Cells are
compiled typed snippets; trusted HTML is not part of the contract. Missing or duplicate
row keys fail closed before rows render.

Routes distinguish an initial search instruction, an empty repository, no matches,
revalidation, rate limiting, timeout, unavailability, and malformed source data.
Navigation state is URL-persisted so SvelteKit can discard superseded navigation
results rather than allowing stale responses to replace newer state.

## Verification

Focused deterministic contracts use `pdm run agent-test <node> -v` and
`pdm run agent-test --frontend <tracked-test-file>`. Disposable PostgreSQL/QLever
contracts use `pdm run agent-test --safe-integration <node> -v`. Configured corpus
contracts use `pdm run agent-test --full-store <node> -v`. The complete acceptance gate
is `pdm run verify`.
