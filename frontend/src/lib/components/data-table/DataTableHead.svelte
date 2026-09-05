<script lang="ts" generics="Row">
	import DataTableFilterCell from './DataTableFilterCell.svelte';
	import DataTableHeaderCell from './DataTableHeaderCell.svelte';
	import type { DataTableColumn, DataTableFilterState, DataTableIntent, DataTableOperations, DataTableSortState } from './types';
	let { columns, operations, filters, sort, stickyHeader, onintent }: {
		columns: readonly DataTableColumn<Row>[]; operations: DataTableOperations;
		filters: Readonly<Record<string, DataTableFilterState>>; sort: DataTableSortState | null;
		stickyHeader: boolean; onintent: (intent: DataTableIntent) => void;
	} = $props();
	const hasFilters = $derived(operations.kind === 'server' && columns.some((column) => column.filter));
	const stickyStyle = (column: DataTableColumn<Row>) => column.sticky ? `${column.sticky.side}: ${column.sticky.offset}px` : undefined;
	const headerClass = (column: DataTableColumn<Row>) => ['border-b border-default px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-muted', column.sticky ? 'sticky bg-card z-30' : ''].filter(Boolean).join(' ');
</script>
<thead class={stickyHeader ? 'sticky top-0 z-30 bg-card' : undefined}>
	<tr>{#each columns as column (column.id)}<DataTableHeaderCell {column} {sort} className={headerClass(column)} style={stickyStyle(column)} {onintent} />{/each}</tr>
	{#if hasFilters}<tr>{#each columns as column (column.id)}<DataTableFilterCell {column} value={filters[column.id]} className={headerClass(column)} style={stickyStyle(column)} {onintent} />{/each}</tr>{/if}
</thead>
