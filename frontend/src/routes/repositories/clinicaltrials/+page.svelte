<script lang="ts">
	import { searchClinicalTrials } from '$lib/api.clinicaltrials';
	import type { CTStudySearchPage } from '$lib/types';
	import { createRepoBrowse } from '$lib/repo-browse.svelte';
	import RepoPageHeader from '$lib/components/RepoPageHeader.svelte';
	import RepoSearchBar from '$lib/components/RepoSearchBar.svelte';
	import RepoResultsCard from '$lib/components/RepoResultsCard.svelte';
	import CtResultsTable from '$lib/components/CtResultsTable.svelte';

	const SUGGESTIONS = ['melanoma', 'breast cancer', 'immunotherapy', 'CAR-T', 'glioblastoma'];

	const search = createRepoBrowse<CTStudySearchPage>((term) =>
		searchClinicalTrials({ condition: term, limit: 25 })
	);

	const countLabel = $derived(search.result ? `${search.result.total.toLocaleString()} trials` : '');
	const isEmpty = $derived((search.result?.studies.length ?? 0) === 0);
</script>

<svelte:head>
	<title>ClinicalTrials.gov · ONTOPRISM</title>
</svelte:head>

<RepoPageHeader
	title="ClinicalTrials.gov"
	description="Search the ClinicalTrials.gov v2 registry by condition. Open a trial to see its interventions, outcomes, eligibility, sponsors, sites, and publication references."
	total={search.result?.total ?? null}
>
	{#snippet help()}
		Enter a medical condition to search interventional and observational studies. Results are
		fetched live from the public ClinicalTrials.gov v2 API. Open a trial for full protocol detail.
	{/snippet}
</RepoPageHeader>

<RepoSearchBar
	bind:value={search.q}
	placeholder="Search trials by condition…"
	ariaLabel="Search ClinicalTrials.gov"
	suggestions={SUGGESTIONS}
	loading={search.loading}
	onsearch={search.search}
	onsuggestion={search.suggest}
	suggestionsLabel="Quick:"
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
				No trials matched “{search.submitted}”.
			</p>
		{:else}
			<CtResultsTable studies={search.result?.studies ?? []} />
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
