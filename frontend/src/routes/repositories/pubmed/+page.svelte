<script lang="ts">
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { navigating, page } from '$app/state';
	import { repositoryGridHref } from '$lib/repository-navigation';
	import { repositorySearchHref } from '$lib/repository-search';
	import RepoPageHeader from '$lib/components/RepoPageHeader.svelte';
	import RepoSearchBar from '$lib/components/RepoSearchBar.svelte';
	import RepoResultsCard from '$lib/components/RepoResultsCard.svelte';
	import PubMedResultsTable from '$lib/components/PubMedResultsTable.svelte';
	import type { PageProps } from './$types';
	import RemoteSearchSurface from '$lib/components/RemoteSearchSurface.svelte';
	import RemoteServiceDisclosure from '$lib/components/RemoteServiceDisclosure.svelte';
	import Pagination from '$lib/components/Pagination.svelte';
	import type { DataTableIntent, DataTableOperations } from '$lib/components/data-table/types';

	let { data }: PageProps = $props();
	let queryValue = $derived(data.query);
	const result = $derived(data.result.state === 'ready' ? data.result.data : null);
	const SUGGESTIONS = ['melanoma immunotherapy', 'CRISPR', 'tumor microenvironment', 'BRCA1'];
	const loading = $derived(navigating.to?.url.pathname === page.url.pathname);
	const countLabel = $derived(result ? `${result.total.toLocaleString()} articles` : '');

	// eslint-disable-next-line svelte/no-navigation-without-resolve -- repositoryGridHref receives the resolved route before appending owned URL state
	function navigate(update: (params: URLSearchParams) => void): void { goto(repositoryGridHref(resolve('/repositories/pubmed'), page.url, update)); }
	function search(term = queryValue): void { const target = repositorySearchHref('pubmed', page.url, term); goto(target); }
	function intent(value: DataTableIntent): void { if (value.kind !== 'sort' && value.kind !== 'reset') return; navigate((params) => { params.delete('offset'); if (value.kind === 'reset') params.delete('size'); if (value.kind === 'sort' && value.sort.key === 'date') params.set('sort', 'pub_date'); else params.delete('sort'); }); }
	const operations = $derived<DataTableOperations>({ kind: 'server', sort: data.sort === 'pub_date' ? { key: 'date', direction: 'desc' } : null, defaultSort: null, activeSortLabel: data.sort === 'pub_date' ? 'Publication date descending' : 'Relevance', filters: {}, busy: loading, onintent: intent });
</script>

<svelte:head>
	<title>PubMed · ONTOPRISM</title>
</svelte:head>

<RepoPageHeader
	title="PubMed"
	kind="remote-live-service"
	description="Search the NCBI PubMed literature database. Open an article for its abstract, authors, MeSH terms, and identifiers."
	total={result?.total ?? null}
>
	{#snippet help()}
		Enter a query (terms, MeSH, author names) to search PubMed via the NCBI E-utilities. Open an
		article for its abstract, MeSH headings, DOI/PMC ids, and a link to PubMed.
	{/snippet}
</RepoPageHeader>

<RemoteServiceDisclosure service="NCBI PubMed" />

<RepoSearchBar
	bind:value={queryValue}
	placeholder="Search PubMed… e.g. melanoma immunotherapy"
	ariaLabel="Search PubMed"
	suggestions={SUGGESTIONS}
	{loading}
	onsearch={search}
	onsuggestion={(term) => {
		queryValue = term;
		search(term);
	}}
/>

<RemoteSearchSurface
	service="PubMed"
	error={data.result.state === 'error' ? data.result : null}
	ready={result !== null}
>
	{#snippet instruction()}
		<p class="text-sm text-muted">
			Enter a query above to search <span class="font-medium text-default">PubMed</span>.
		</p>
	{/snippet}
	<RepoResultsCard
		title={`Results for “${data.query}”`}
		{countLabel}
		{loading}
		error={null}
	>
		<PubMedResultsTable articles={result?.articles ?? []} {operations} emptyMessage={`No articles matched “${data.query}”.`} />
		<Pagination offset={data.offset} limit={data.size} total={result?.total ?? 0} navigationTotal={Math.min(result?.total ?? 0, 10000)} onPage={(offset) => navigate((params) => { if (offset) params.set('offset', String(offset)); else params.delete('offset'); })} onSize={(size) => navigate((params) => { params.delete('offset'); if (size === 25) params.delete('size'); else params.set('size', String(size)); })} />
	</RepoResultsCard>
</RemoteSearchSurface>
