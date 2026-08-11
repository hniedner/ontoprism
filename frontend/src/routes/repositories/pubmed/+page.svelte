<script lang="ts">
	import { goto } from '$app/navigation';
	import { navigating, page } from '$app/state';
	import { repositorySearchHref } from '$lib/repository-search';
	import RepoPageHeader from '$lib/components/RepoPageHeader.svelte';
	import RepoSearchBar from '$lib/components/RepoSearchBar.svelte';
	import RepoResultsCard from '$lib/components/RepoResultsCard.svelte';
	import PubMedResultsTable from '$lib/components/PubMedResultsTable.svelte';
	import type { PageProps } from './$types';

	const SUGGESTIONS = ['melanoma immunotherapy', 'CRISPR', 'tumor microenvironment', 'BRCA1'];

	let { data }: PageProps = $props();
	let queryValue = $derived(data.query);
	const result = $derived(data.result.state === 'ready' ? data.result.data : null);
	const loading = $derived(navigating.to?.url.pathname === page.url.pathname);
	const countLabel = $derived(result ? `${result.total.toLocaleString()} articles` : '');
	const isEmpty = $derived((result?.articles.length ?? 0) === 0);

	function search(term = queryValue): void {
		goto(repositorySearchHref('pubmed', page.url, term));
	}
</script>

<svelte:head>
	<title>PubMed · ONTOPRISM</title>
</svelte:head>

<RepoPageHeader
	title="PubMed"
	description="Search the NCBI PubMed literature database. Open an article for its abstract, authors, MeSH terms, and identifiers."
	total={result?.total ?? null}
>
	{#snippet help()}
		Enter a query (terms, MeSH, author names) to search PubMed via the NCBI E-utilities. Open an
		article for its abstract, MeSH headings, DOI/PMC ids, and a link to PubMed.
	{/snippet}
</RepoPageHeader>

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

{#if result}
	<RepoResultsCard
		title={`Results for “${data.query}”`}
		{countLabel}
		{loading}
		error={null}
	>
		{#if isEmpty}
			<p class="px-4 py-6 text-center text-sm text-muted">
				No articles matched “{data.query}”.
			</p>
		{:else}
			<PubMedResultsTable articles={result.articles} />
		{/if}
	</RepoResultsCard>
{:else}
	<div class="rounded-xl border border-dashed border-default bg-card/50 px-6 py-12 text-center">
		<p class="text-sm text-muted">
			Enter a query above to search <span class="font-medium text-default">PubMed</span>.
		</p>
	</div>
{/if}
