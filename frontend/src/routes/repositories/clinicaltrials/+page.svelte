<script lang="ts">
	import { goto } from '$app/navigation';
	import { navigating, page } from '$app/state';
	import { repositorySearchHref } from '$lib/repository-search';
	import RepoPageHeader from '$lib/components/RepoPageHeader.svelte';
	import RepoSearchBar from '$lib/components/RepoSearchBar.svelte';
	import RepoResultsCard from '$lib/components/RepoResultsCard.svelte';
	import CtResultsTable from '$lib/components/CtResultsTable.svelte';
	import type { PageProps } from './$types';

	const SUGGESTIONS = ['melanoma', 'breast cancer', 'immunotherapy', 'CAR-T', 'glioblastoma'];

	let { data }: PageProps = $props();
	let queryValue = $derived(data.query);
	const result = $derived(data.result.state === 'ready' ? data.result.data : null);
	const loading = $derived(navigating.to?.url.pathname === page.url.pathname);
	const countLabel = $derived(result ? `${result.total.toLocaleString()} trials` : '');
	const isEmpty = $derived((result?.studies.length ?? 0) === 0);

	function search(term = queryValue): void {
		goto(repositorySearchHref('clinicaltrials', page.url, term));
	}
</script>

<svelte:head>
	<title>ClinicalTrials.gov · ONTOPRISM</title>
</svelte:head>

<RepoPageHeader
	title="ClinicalTrials.gov"
	description="Search the ClinicalTrials.gov v2 registry by condition. Open a trial to see its interventions, outcomes, eligibility, sponsors, sites, and publication references."
	total={result?.total ?? null}
>
	{#snippet help()}
		Enter a medical condition to search interventional and observational studies. Results are
		fetched live from the public ClinicalTrials.gov v2 API. Open a trial for full protocol detail.
	{/snippet}
</RepoPageHeader>

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

{#if result}
	<RepoResultsCard
		title={`Results for “${data.query}”`}
		{countLabel}
		{loading}
		error={null}
	>
		{#if isEmpty}
			<p class="px-4 py-6 text-center text-sm text-muted">
				No trials matched “{data.query}”.
			</p>
		{:else}
			<CtResultsTable studies={result.studies} />
		{/if}
	</RepoResultsCard>
{:else}
	<div class="rounded-xl border border-dashed border-default bg-card/50 px-6 py-12 text-center">
		<p class="text-sm text-muted">
			Enter a condition above to search
			<span class="font-medium text-default">ClinicalTrials.gov</span>.
		</p>
	</div>
{/if}
