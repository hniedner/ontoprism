<script lang="ts">
	import { resolve } from '$app/paths';
	import type { CTStudySummary } from '$lib/types';
	import DataTable from '$lib/components/data-table/DataTable.svelte';
	import type { DataTableColumn } from '$lib/components/data-table/types';

	let { studies }: { studies: readonly CTStudySummary[] } = $props();
	const operations = { kind: 'client-page', scopeLabel: 'Filters and sorting apply only to the trials loaded on this page.' } as const;
	const columns: readonly DataTableColumn<CTStudySummary>[] = [
		{ id: 'nct_id', label: 'NCT ID', cell: idCell, sortValue: (trial) => trial.nct_id, filter: { value: (trial) => trial.nct_id, ariaLabel: 'Filter loaded trial IDs' }, sticky: { side: 'left', offset: 0 } },
		{ id: 'title', label: 'Title', cell: titleCell, sortValue: (trial) => trial.title, filter: { value: (trial) => `${trial.title} ${trial.conditions.join(' ')}`, ariaLabel: 'Filter loaded trial titles and conditions' } },
		{ id: 'status', label: 'Status', cell: statusCell, sortValue: (trial) => trial.status, filter: { value: (trial) => trial.status, ariaLabel: 'Filter loaded trial statuses' } },
		{ id: 'phase', label: 'Phase', cell: phaseCell, sortValue: (trial) => trial.phase, filter: { value: (trial) => trial.phase, ariaLabel: 'Filter loaded trial phases' } }
	];
</script>

{#snippet idCell(trial: CTStudySummary)}
	<a href={resolve('/repositories/clinicaltrials/[nct]', { nct: trial.nct_id })} class="rounded bg-subtle px-1.5 py-0.5 font-mono text-xs text-primary-600 no-underline hover:text-primary-700 dark:text-primary-400">{trial.nct_id}</a>
{/snippet}
{#snippet titleCell(trial: CTStudySummary)}
	<a href={resolve('/repositories/clinicaltrials/[nct]', { nct: trial.nct_id })} class="font-medium text-default no-underline hover:text-primary-600">{trial.title}</a>
	{#if trial.conditions.length}<span class="ml-1 text-xs text-subtle">{trial.conditions.join(', ')}</span>{/if}
{/snippet}
{#snippet statusCell(trial: CTStudySummary)}<span class="text-muted">{trial.status ?? '—'}</span>{/snippet}
{#snippet phaseCell(trial: CTStudySummary)}
	{#if trial.phase}<span class="rounded-md bg-info-50 px-2 py-0.5 text-xs font-medium text-info dark:bg-info-900/30">{trial.phase}</span>{:else}<span class="text-muted">—</span>{/if}
{/snippet}

<DataTable rows={studies} {columns} caption="ClinicalTrials.gov results loaded on this page" regionLabel="ClinicalTrials.gov loaded-page results" getRowId={(trial) => trial.nct_id} {operations} stickyHeader={true} />
