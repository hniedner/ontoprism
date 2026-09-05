<script lang="ts">
	import { resolve } from '$app/paths';
	import type { CTStudySummary } from '$lib/types';
	import DataTable from '$lib/components/data-table/DataTable.svelte';
	import type { DataTableColumn, DataTableOperations } from '$lib/components/data-table/types';

	let { studies, operations = { kind: 'none' } }: { studies: readonly CTStudySummary[]; operations?: DataTableOperations } = $props();
	const interactive = $derived(operations.kind === 'server');
	let columns = $derived.by((): readonly DataTableColumn<CTStudySummary>[] => [
		{ id: 'nct_id', label: 'NCT ID', cell: idCell, sticky: { side: 'left', offset: 0 } },
		{ id: 'title', label: 'Title', cell: titleCell },
		{ id: 'status', label: 'Status', cell: statusCell, filter: interactive ? { kind: 'categorical', ariaLabel: 'Filter trial statuses', options: ['ACTIVE_NOT_RECRUITING', 'APPROVED_FOR_MARKETING', 'AVAILABLE', 'COMPLETED', 'ENROLLING_BY_INVITATION', 'NOT_YET_RECRUITING', 'NO_LONGER_AVAILABLE', 'RECRUITING', 'SUSPENDED', 'TEMPORARILY_NOT_AVAILABLE', 'TERMINATED', 'UNKNOWN', 'WITHDRAWN', 'WITHHELD'].map((value) => ({ value, label: value.replaceAll('_', ' ') })) } : undefined },
		{ id: 'phase', label: 'Phase', cell: phaseCell, filter: interactive ? { kind: 'categorical', ariaLabel: 'Filter trial phases', options: ['EARLY_PHASE1', 'PHASE1', 'PHASE2', 'PHASE3', 'PHASE4'].map((value) => ({ value, label: value.replace('_', ' ') })) } : undefined }
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
	{#if trial.phase}<span class="rounded-md bg-info-50 px-2 py-0.5 text-xs font-medium text-info dark:bg-info-900/30">{trial.phase}</span>{:else}<span class="text-muted">—</span>{/if}
{/snippet}

<DataTable rows={studies} {columns} caption="ClinicalTrials.gov repository results" regionLabel="ClinicalTrials.gov repository results" getRowId={(trial) => trial.nct_id} {operations} stickyHeader={true} />
