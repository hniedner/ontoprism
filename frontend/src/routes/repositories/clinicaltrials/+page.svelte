<script lang="ts">
	import { resolve } from '$app/paths';
	import CtResultsTable from '$lib/components/CtResultsTable.svelte';
	import RepoBrowsePage from '$lib/components/RepoBrowsePage.svelte';
	import type { DataTableOperations } from '$lib/components/data-table/types';
	import { gridControls } from '$lib/repository-registry';
	import type { CTStudySummary } from '$lib/types';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
	const controls = gridControls('clinicaltrials');
	const response = $derived(data.result.state === 'ready' ? data.result.data : null);
	const remote = $derived.by(() => data.result.state === 'error'
		? {
				service: 'ClinicalTrials.gov' as const,
				state: 'error' as const,
				error: { remoteState: data.result.remoteState, message: data.result.message }
			}
		: {
				service: 'ClinicalTrials.gov' as const,
				state: data.result.state,
				error: null
			});
</script>

<RepoBrowsePage
	title="ClinicalTrials.gov"
	route={resolve('/repositories/clinicaltrials')}
	kind="remote-live-service"
	description="Search interventional and observational studies registered at ClinicalTrials.gov. Results are retrieved live from the ClinicalTrials.gov API."
	placeholder="Search by condition… e.g. melanoma"
	ariaLabel="Search clinical trials"
	suggestions={['melanoma', 'breast cancer', 'lung cancer', 'glioblastoma', 'leukemia']}
	suggestionsLabel="Conditions:"
	browseTitle="Clinical trials"
	initial={{ result: { total: response?.total ?? 0, hits: response?.studies ?? [] }, query: data.query, offset: 0, size: data.size, sort: String('relevance'), filters: data.filters }}
	defaultSort="relevance"
	sortKeys={controls.sortKeys}
	filterKeys={controls.filterKeys}
	textKeys={controls.textKeys}
	{remote}
	cursor={{ trail: data.cursors, next: response?.next_page_token ?? null }}
	countLabel={(n: number) => `${n.toLocaleString()} trials`}
	noMatchesLabel={(query: string) => `No trials matched “${query}”.`}
>
	{#snippet helpText()}
		Search by condition. Status and phase filters are sent to ClinicalTrials.gov with the query. Click a trial to view its study details.
	{/snippet}
	{#snippet instruction()}
		<p class="text-sm text-muted">Enter a condition above to search ClinicalTrials.gov.</p>
	{/snippet}
	{#snippet results(studies: CTStudySummary[], operations: DataTableOperations, emptyMessage: string)}
		<CtResultsTable {studies} {operations} {emptyMessage} />
	{/snippet}
</RepoBrowsePage>
