<script lang="ts">
	import { resolve } from '$app/paths';
	import { CT_PHASES, CT_STATUSES, type CTStudySummary } from '$lib/types';
	import DataTable from '$lib/components/data-table/DataTable.svelte';
	import type { DataTableColumn, DataTableOperations } from '$lib/components/data-table/types';

	let { studies, operations = { kind: 'none' }, emptyMessage = 'No trials.' }: { studies: readonly CTStudySummary[]; operations?: DataTableOperations; emptyMessage?: string } = $props();
	const interactive = $derived(operations.kind === 'server');
	let columns = $derived.by((): readonly DataTableColumn<CTStudySummary>[] => [
		{ id: 'nct_id', label: 'NCT ID', cell: idCell, sticky: { side: 'left', offset: 0 } },
		{ id: 'title', label: 'Title', cell: titleCell },
		{ id: 'status', label: 'Status', cell: statusCell, filter: interactive ? { kind: 'categorical', ariaLabel: 'Filter trial statuses', options: CT_STATUSES.map((value) => ({ value, label: value.replaceAll('_', ' ') })) } : undefined },
		{ id: 'phase', label: 'Phase', cell: phaseCell, filter: interactive ? { kind: 'categorical', ariaLabel: 'Filter trial phases', options: CT_PHASES.map((value) => ({ value, label: value.replace('_', ' ') })) } : undefined }
	]);
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
	{#if trial.phase.length}<span class="rounded-md bg-info-50 px-2 py-0.5 text-xs font-medium text-info dark:bg-info-900/30">{trial.phase.join(', ')}</span>{:else}<span class="text-muted">—</span>{/if}
{/snippet}

<DataTable rows={studies} {columns} caption="ClinicalTrials.gov repository results" regionLabel="ClinicalTrials.gov repository results" getRowId={(trial) => trial.nct_id} {operations} {emptyMessage} stickyHeader={true} />
