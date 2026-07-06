<script lang="ts">
	import { searchClinicalTrials } from '$lib/api.clinicaltrials';
	import type { CTStudySearchPage } from '$lib/types';
	import RepoPageHeader from '$lib/components/RepoPageHeader.svelte';
	import RepoSearchBar from '$lib/components/RepoSearchBar.svelte';
	import RepoResultsCard from '$lib/components/RepoResultsCard.svelte';
	import CtResultsTable from '$lib/components/CtResultsTable.svelte';

	const SUGGESTIONS = ['melanoma', 'breast cancer', 'immunotherapy', 'CAR-T', 'glioblastoma'];

	let condition = $state('');
	let submitted = $state('');
	let result = $state<CTStudySearchPage | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);

	async function load(term: string) {
		const trimmed = term.trim();
		if (!trimmed) return;
		loading = true;
		error = null;
		try {
			result = await searchClinicalTrials({ condition: trimmed, limit: 25 });
			submitted = trimmed;
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
			result = null;
		} finally {
			loading = false;
		}
	}

	const countLabel = $derived(result ? `${result.total.toLocaleString()} trials` : '');
	const isEmpty = $derived((result?.studies.length ?? 0) === 0);
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
	bind:value={condition}
	placeholder="Search trials by condition…"
	ariaLabel="Search ClinicalTrials.gov"
	suggestions={SUGGESTIONS}
	{loading}
	onsearch={() => load(condition)}
	onsuggestion={(term) => {
		condition = term;
		load(term);
	}}
	suggestionsLabel="Quick:"
/>

{#if result || error}
	<RepoResultsCard title={`Results for “${submitted}”`} {countLabel} {loading} {error}>
		{#if isEmpty}
			<p class="px-4 py-6 text-center text-sm text-muted">No trials matched “{submitted}”.</p>
		{:else}
			<CtResultsTable studies={result?.studies ?? []} />
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
