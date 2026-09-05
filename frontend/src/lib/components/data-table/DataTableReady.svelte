<script lang="ts" generics="Row">
	import { untrack } from 'svelte';
	import DataTableBody from './DataTableBody.svelte';
	import DataTableHead from './DataTableHead.svelte';
	import { filterRows, sortRows } from './data-table';
	import type { DataTableColumn, DataTableReadyProps, DataTableSortDirection } from './types';

	let { rows, columns, caption, regionLabel, getRowId, operations, initialSort, emptyMessage, stickyHeader }: DataTableReadyProps<Row> = $props();
	const startingSort = untrack(() => initialSort);
	let sortColumnId = $state<string | null>(startingSort?.columnId ?? null);
	let sortDirection = $state<DataTableSortDirection>(startingSort?.direction ?? 'asc');
	let filters = $state<Record<string, string>>({});
	let hasActiveFilters = $derived(Object.values(filters).some((value) => value.trim()));
	let displayedRows = $derived.by(() => {
		const filtered = operations.kind === 'client-page' ? filterRows(rows, columns, filters) : [...rows];
		const sortedColumn = columns.find((column) => column.id === sortColumnId);
		return sortedColumn?.sortValue ? sortRows(filtered, sortedColumn.sortValue, sortDirection) : filtered;
	});

	function toggleSort(column: DataTableColumn<Row>): void {
		if (!column.sortValue) return;
		if (sortColumnId === column.id) sortDirection = sortDirection === 'asc' ? 'desc' : 'asc';
		else {
			sortColumnId = column.id;
			sortDirection = 'asc';
		}
	}
</script>

{#if operations.kind === 'client-page'}
	<div class="flex items-center justify-between gap-3 px-4 py-2 text-xs text-muted">
		<span>{operations.scopeLabel}</span>
		{#if hasActiveFilters}<button type="button" class="underline" onclick={() => (filters = {})}>Clear page-local filters</button>{/if}
	</div>
{/if}
<!-- The labelled scrollable region needs a tab stop so keyboard users can scroll it. -->
<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
<div class="overflow-x-auto" role="region" aria-label={regionLabel} tabindex="0">
	<table class="table-auto min-w-full border-separate border-spacing-0 text-sm">
		<caption class="sr-only">{caption}</caption>
		<DataTableHead {columns} {operations} {filters} {sortColumnId} {sortDirection} {stickyHeader} onsort={toggleSort} onfilter={(columnId, value) => (filters = { ...filters, [columnId]: value })} />
		<DataTableBody rows={displayedRows} sourceRowsPresent={rows.length > 0} {columns} {getRowId} {hasActiveFilters} {emptyMessage} />
	</table>
</div>
