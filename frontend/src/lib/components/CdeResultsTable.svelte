<script lang="ts">
	import { resolve } from '$app/paths';
	import type { CdeSummary } from '$lib/types';
	import DataTable from '$lib/components/data-table/DataTable.svelte';
	import type { DataTableColumn, DataTableOperations } from '$lib/components/data-table/types';

	let { hits, operations = { kind: 'none' } }: { hits: readonly CdeSummary[]; operations?: DataTableOperations } = $props();
	const interactive = $derived(operations.kind === 'server');
	let columns = $derived.by((): readonly DataTableColumn<CdeSummary>[] => [
		{ id: 'public_id', label: 'Public ID', cell: idCell, sortable: interactive ? ['asc', 'desc'] : undefined, sticky: { side: 'left', offset: 0 } },
		{ id: 'name', label: 'Name', cell: nameCell, sortable: interactive ? ['asc', 'desc'] : undefined },
		{ id: 'context', label: 'Context', cell: contextCell },
		{ id: 'datatype', label: 'Type', cell: datatypeCell }
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
{#snippet datatypeCell(cde: CdeSummary)}
	{#if cde.datatype}<span class="rounded-md bg-info-50 px-2 py-0.5 text-xs font-medium text-info dark:bg-info-900/30">{cde.datatype}</span>{:else}<span class="text-muted">—</span>{/if}
{/snippet}

<DataTable rows={hits} {columns} caption="caDSR CDE repository results" regionLabel="caDSR CDE repository results" getRowId={(cde) => `${cde.public_id}\u0000${cde.version}`} {operations} stickyHeader={true} />
