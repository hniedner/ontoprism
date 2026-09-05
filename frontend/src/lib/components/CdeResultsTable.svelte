<script lang="ts">
	import { resolve } from '$app/paths';
	import type { CdeSummary } from '$lib/types';
	import DataTable from '$lib/components/data-table/DataTable.svelte';
	import type { DataTableColumn } from '$lib/components/data-table/types';

	let { hits }: { hits: readonly CdeSummary[] } = $props();
	const operations = { kind: 'client-page', scopeLabel: 'Filters and sorting apply only to the rows loaded on this page.' } as const;
	const columns: readonly DataTableColumn<CdeSummary>[] = [
		{ id: 'public_id', label: 'Public ID', cell: idCell, sortValue: (cde) => cde.public_id, filterValue: (cde) => `${cde.public_id} ${cde.version}`, filterAriaLabel: 'Filter loaded CDE public IDs and versions' },
		{ id: 'name', label: 'Name', cell: nameCell, sortValue: (cde) => cde.long_name, filterValue: (cde) => `${cde.long_name} ${cde.short_name}`, filterAriaLabel: 'Filter loaded CDE names' },
		{ id: 'context', label: 'Context', cell: contextCell, sortValue: (cde) => cde.context, filterValue: (cde) => cde.context, filterAriaLabel: 'Filter loaded CDE contexts' },
		{ id: 'datatype', label: 'Type', cell: datatypeCell, sortValue: (cde) => cde.datatype, filterValue: (cde) => cde.datatype, filterAriaLabel: 'Filter loaded CDE datatypes' }
	];
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

<DataTable rows={hits} {columns} caption="caDSR CDE results loaded on this page" regionLabel="caDSR CDE loaded-page results" getRowId={(cde) => `${cde.public_id}\u0000${cde.version}`} {operations} />
