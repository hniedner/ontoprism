<script lang="ts" generics="Row">
	import DataTableHeaderCell from './DataTableHeaderCell.svelte';
	import type { DataTableColumn, DataTableFilterState, DataTableIntent, DataTableSortState } from './types';
	let { columns, filters, sort, stickyHeader, onintent }: {
		columns: readonly DataTableColumn<Row>[];
		filters: Readonly<Record<string, DataTableFilterState>>; sort: DataTableSortState | null;
		stickyHeader: boolean; onintent: (intent: DataTableIntent) => void;
	} = $props();
	let openFilterId = $state<string | null>(null);
	const stickyStyle = (column: DataTableColumn<Row>) => column.sticky ? `${column.sticky.side}: ${column.sticky.offset}px` : undefined;
	const headerClass = (column: DataTableColumn<Row>) => ['border-b border-default px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-muted', column.sticky ? 'sticky bg-card z-30' : ''].filter(Boolean).join(' ');
	function toggleFilter(columnId: string): void { openFilterId = openFilterId === columnId ? null : columnId; }
	function closeFilter(columnId: string): void { if (openFilterId === columnId) openFilterId = null; }
</script>
<thead class={stickyHeader ? 'sticky top-0 z-30 bg-card' : undefined}>
	<tr>{#each columns as column (column.id)}<DataTableHeaderCell {column} {sort} filterValue={filters[column.id]} filterOpen={openFilterId === column.id} className={headerClass(column)} style={stickyStyle(column)} {onintent} ontogglefilter={toggleFilter} onclosefilter={closeFilter} />{/each}</tr>
</thead>
