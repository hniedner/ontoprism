<script lang="ts">
	import { resolve } from '$app/paths';
	import type { CdeSummary } from '$lib/types';
	import DataTable from '$lib/components/data-table/DataTable.svelte';
	import type { DataTableColumn, DataTableOperations } from '$lib/components/data-table/types';
	import { columnFilter } from '$lib/repository-registry';
	import type { CadsrFilterDomains } from '$lib/types';

	let { hits, operations = { kind: 'none' }, emptyMessage = 'No records.', domains = {} }: { hits: readonly CdeSummary[]; operations?: DataTableOperations; emptyMessage?: string; domains?: Partial<CadsrFilterDomains> } = $props();
	const interactive = $derived(operations.kind === 'server');
	let columns = $derived.by((): readonly DataTableColumn<CdeSummary>[] => [
		{ id: 'public_id', label: 'Public ID', cell: idCell, sortable: interactive ? ['asc', 'desc'] : undefined, filter: interactive ? columnFilter('cadsr', 'public_id', 'Filter caDSR public IDs') : undefined, sticky: { side: 'left', offset: 0 } },
		{ id: 'name', label: 'Name', cell: nameCell, sortable: interactive ? ['asc', 'desc'] : undefined, filter: interactive ? columnFilter('cadsr', 'name', 'Filter caDSR names') : undefined },
		{ id: 'value_domain_type', label: 'Value domain', cell: valueDomainCell, filter: interactive ? columnFilter('cadsr', 'value_domain_type', 'Filter caDSR value domain types', domains.value_domain_type) : undefined },
		{ id: 'workflow_status', label: 'Workflow', cell: workflowCell, filter: interactive ? columnFilter('cadsr', 'workflow_status', 'Filter caDSR workflow statuses', domains.workflow_status) : undefined },
		{ id: 'registration_status', label: 'Registration', cell: registrationCell, filter: interactive ? columnFilter('cadsr', 'registration_status', 'Filter caDSR registration statuses', domains.registration_status) : undefined },
		{ id: 'context', label: 'Context', cell: contextCell, filter: interactive ? columnFilter('cadsr', 'context', 'Filter caDSR contexts', domains.context) : undefined },
		{ id: 'datatype', label: 'Type', cell: datatypeCell, filter: interactive ? columnFilter('cadsr', 'datatype', 'Filter caDSR datatypes', domains.datatype) : undefined }
	]);
</script>

{#snippet idCell(cde: CdeSummary)}
	<a href={resolve('/repositories/cadsr/[id]', { id: cde.public_id })} class="rounded bg-subtle px-1.5 py-0.5 font-mono text-xs text-primary-600 no-underline hover:text-primary-700 dark:text-primary-400">{cde.public_id}</a>
	<span class="ml-1 text-xs text-subtle">v{cde.version}</span>
{/snippet}
{#snippet nameCell(cde: CdeSummary)}
	<a href={resolve('/repositories/cadsr/[id]', { id: cde.public_id })} class="font-medium text-default no-underline hover:text-primary-600">{cde.long_name}</a>
	{#if cde.short_name}<span class="ml-1 font-mono text-xs text-subtle">{cde.short_name}</span>{/if}
{/snippet}
{#snippet contextCell(cde: CdeSummary)}<span class="text-muted">{cde.context ?? '—'}</span>{/snippet}
{#snippet valueDomainCell(cde: CdeSummary)}<span class="text-muted">{cde.value_domain_type ?? '—'}</span>{/snippet}
{#snippet workflowCell(cde: CdeSummary)}<span class="text-muted">{cde.workflow_status ?? '—'}</span>{/snippet}
{#snippet registrationCell(cde: CdeSummary)}<span class="text-muted">{cde.registration_status ?? '—'}</span>{/snippet}
{#snippet datatypeCell(cde: CdeSummary)}
	{#if cde.datatype}<span class="rounded-md bg-info-50 px-2 py-0.5 text-xs font-medium text-info dark:bg-info-900/30">{cde.datatype}</span>{:else}<span class="text-muted">—</span>{/if}
{/snippet}

<DataTable rows={hits} {columns} caption="caDSR CDE repository results" regionLabel="caDSR CDE repository results" getRowId={(cde) => `${cde.public_id}\u0000${cde.version}`} {operations} {emptyMessage} stickyHeader={true} />
