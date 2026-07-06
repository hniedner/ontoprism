<script lang="ts">
	import { searchPubmed } from '$lib/api.pubmed';
	import type { PubMedSearchResult } from '$lib/types';
	import { createRepoBrowse } from '$lib/repo-browse.svelte';
	import RepoPageHeader from '$lib/components/RepoPageHeader.svelte';
	import RepoSearchBar from '$lib/components/RepoSearchBar.svelte';
	import RepoResultsCard from '$lib/components/RepoResultsCard.svelte';
	import PubMedResultsTable from '$lib/components/PubMedResultsTable.svelte';

	const SUGGESTIONS = ['melanoma immunotherapy', 'CRISPR', 'tumor microenvironment', 'BRCA1'];

	const search = createRepoBrowse<PubMedSearchResult>((term) => searchPubmed(term, 25));

	const countLabel = $derived(
		search.result ? `${search.result.total.toLocaleString()} articles` : ''
	);
	const isEmpty = $derived((search.result?.articles.length ?? 0) === 0);
</script>

<svelte:head>
	<title>PubMed · ONTOPRISM</title>
</svelte:head>

<RepoPageHeader
	title="PubMed"
	description="Search the NCBI PubMed literature database. Open an article for its abstract, authors, MeSH terms, and identifiers."
	total={search.result?.total ?? null}
>
	{#snippet help()}
		Enter a query (terms, MeSH, author names) to search PubMed via the NCBI E-utilities. Open an
		article for its abstract, MeSH headings, DOI/PMC ids, and a link to PubMed.
	{/snippet}
</RepoPageHeader>

<RepoSearchBar
	bind:value={search.q}
	placeholder="Search PubMed… e.g. melanoma immunotherapy"
	ariaLabel="Search PubMed"
	suggestions={SUGGESTIONS}
	loading={search.loading}
	onsearch={search.search}
	onsuggestion={search.suggest}
/>

{#if search.result || search.error}
	<RepoResultsCard
		title={`Results for “${search.submitted}”`}
		{countLabel}
		loading={search.loading}
		error={search.error}
	>
		{#if isEmpty}
			<p class="px-4 py-6 text-center text-sm text-muted">
				No articles matched “{search.submitted}”.
			</p>
		{:else}
			<PubMedResultsTable articles={search.result?.articles ?? []} />
		{/if}
	</RepoResultsCard>
{:else}
	<div class="rounded-xl border border-dashed border-default bg-card/50 px-6 py-12 text-center">
		<p class="text-sm text-muted">
			Enter a query above to search <span class="font-medium text-default">PubMed</span>.
		</p>
	</div>
{/if}
