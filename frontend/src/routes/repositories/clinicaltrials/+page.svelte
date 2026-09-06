<script lang="ts">
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { navigating, page } from '$app/state';
	import { clearGridFilters, navigateRepositoryGrid } from '$lib/repository-navigation';
	import { repositorySearchHref } from '$lib/repository-search';
	import RepoPageHeader from '$lib/components/RepoPageHeader.svelte';
	import RepoSearchBar from '$lib/components/RepoSearchBar.svelte';
	import RepoResultsCard from '$lib/components/RepoResultsCard.svelte';
	import CtResultsTable from '$lib/components/CtResultsTable.svelte';
	import type { PageProps } from './$types';
	import RemoteSearchSurface from '$lib/components/RemoteSearchSurface.svelte';
	import RemoteServiceDisclosure from '$lib/components/RemoteServiceDisclosure.svelte';
	import CursorPagination from '$lib/components/CursorPagination.svelte';
	import type { DataTableFilterState, DataTableIntent, DataTableOperations } from '$lib/components/data-table/types';

	const SUGGESTIONS = ['melanoma', 'breast cancer', 'immunotherapy', 'CAR-T', 'glioblastoma'];

	let { data }: PageProps = $props();
	let queryValue = $derived(data.query);
	const result = $derived(data.result.state === 'ready' ? data.result.data : null);
	const loading = $derived(navigating.to?.url.pathname === page.url.pathname);
	const countLabel = $derived(result ? `${result.total.toLocaleString()} trials` : '');

	function navigate(update: (params: URLSearchParams) => void): void { navigateRepositoryGrid(resolve('/repositories/clinicaltrials'), page.url, update, goto); }
	function search(term = queryValue): void { goto(repositorySearchHref('clinicaltrials', page.url, term)); }
	const filters = $derived<Record<string, DataTableFilterState>>(Object.fromEntries(Object.entries(data.filters).map(([key, selected]) => [key, { kind: 'categorical', selected }])));
	function intent(value: DataTableIntent): void { void navigate((params) => { params.delete('cursor'); if (value.kind === 'filter') { params.delete(value.columnId); for (const selected of value.filter.selected) params.append(value.columnId, selected); } else if (value.kind === 'clear-filter') params.delete(value.columnId); else if (value.kind === 'clear-filters' || value.kind === 'reset') { clearGridFilters(params, Object.keys(data.filters)); if (value.kind === 'reset') params.delete('size'); } }); }
	const operations = $derived<DataTableOperations>({ kind: 'server', sort: null, defaultSort: null, activeSortLabel: 'ClinicalTrials.gov relevance', filters, busy: loading, onintent: intent });
</script>

<svelte:head>
	<title>ClinicalTrials.gov · ONTOPRISM</title>
</svelte:head>

<RepoPageHeader
	title="ClinicalTrials.gov"
	kind="remote-live-service"
	description="Search the ClinicalTrials.gov v2 registry by condition. Open a trial to see its interventions, outcomes, eligibility, sponsors, sites, and publication references."
	total={result?.total ?? null}
>
	{#snippet help()}
		Enter a medical condition to search interventional and observational studies. Results are
		fetched live from the public ClinicalTrials.gov v2 API. Open a trial for full protocol detail.
	{/snippet}
</RepoPageHeader>

<RemoteServiceDisclosure service="ClinicalTrials.gov" />

<RepoSearchBar
	bind:value={queryValue}
	placeholder="Search trials by condition…"
	ariaLabel="Search ClinicalTrials.gov"
	suggestions={SUGGESTIONS}
	{loading}
	onsearch={search}
	onsuggestion={(term) => {
		queryValue = term;
		search(term);
	}}
	suggestionsLabel="Quick:"
/>

<RemoteSearchSurface
	service="ClinicalTrials.gov"
	error={data.result.state === 'error' ? data.result : null}
	ready={result !== null}
>
	{#snippet instruction()}
		<p class="text-sm text-muted">
			Enter a condition above to search
			<span class="font-medium text-default">ClinicalTrials.gov</span>.
		</p>
	{/snippet}
	<RepoResultsCard
		title={`Results for “${data.query}”`}
		{countLabel}
		{loading}
		error={null}
	>
		<CtResultsTable studies={result?.studies ?? []} {operations} emptyMessage={`No trials matched “${data.query}”.`} />
		<CursorPagination count={result?.studies.length ?? 0} total={result?.total ?? 0} hasPrevious={data.cursors.length > 0} hasNext={Boolean(result?.next_page_token)} size={data.size} onPrevious={() => navigate((params) => { const trail = params.getAll('cursor'); params.delete('cursor'); for (const cursor of trail.slice(0, -1)) params.append('cursor', cursor); })} onNext={() => { if (result?.next_page_token) navigate((params) => params.append('cursor', result.next_page_token!)); }} onSize={(size) => navigate((params) => { params.delete('cursor'); if (size === 25) params.delete('size'); else params.set('size', String(size)); })} />
	</RepoResultsCard>
</RemoteSearchSurface>
