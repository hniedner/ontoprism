<script lang="ts">
	import { resolve } from '$app/paths';
	import type { CdeRepositorySort, CdeSummary } from '$lib/types';
	import RepoBrowsePage from '$lib/components/RepoBrowsePage.svelte';
	import CdeResultsTable from '$lib/components/CdeResultsTable.svelte';
	import type { PageProps } from './$types';
	import type { DataTableOperations } from '$lib/components/data-table/types';
	import { gridControls } from '$lib/repository-registry';

	const SUGGESTIONS = ['tumor stage', 'age at diagnosis', 'race', 'gender', 'treatment response'];
	let { data }: PageProps = $props();
	const controls = $derived(gridControls('cadsr', data.domains));
	const sortKeys = $derived(controls.sortKeys as Record<string, Partial<Record<'asc' | 'desc', CdeRepositorySort>>>);
</script>

<RepoBrowsePage
	title="caDSR CDEs"
	route={resolve('/repositories/cadsr')}
	description="Browse and search caDSR Common Data Elements. Each CDE links to NCIt concepts (ISO-11179 roles), permissible values, and semantically similar elements."
	placeholder="Search by name, definition, or CDE ID…"
	ariaLabel="Search caDSR"
	suggestions={SUGGESTIONS}
	suggestionsLabel="Quick:"
	browseTitle="Browsing all CDEs"
	initial={data.initial}
	defaultSort="source"
	{sortKeys}
	filterKeys={controls.filterKeys}
	textKeys={controls.textKeys}
	countLabel={(n: number) => `${n.toLocaleString()} CDEs`}
>
	{#snippet helpText()}
		Search CDEs by name, long name, or public ID. Open a CDE to see its NCIt concept mappings,
		permissible values, and embedding-based similar CDEs. Concept links cross-navigate to the NCIt
		browser.
	{/snippet}
	{#snippet results(hits: CdeSummary[], operations: DataTableOperations, emptyMessage: string)}
		<CdeResultsTable {hits} {operations} {emptyMessage} domains={data.domains} />
	{/snippet}
</RepoBrowsePage>
