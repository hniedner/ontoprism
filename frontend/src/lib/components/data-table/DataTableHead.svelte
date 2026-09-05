<script lang="ts" generics="Row">
	import type { DataTableColumn, DataTableOperations, DataTableSortDirection } from './types';
	import DataTableFilterCell from './DataTableFilterCell.svelte';
	import DataTableHeaderCell from './DataTableHeaderCell.svelte';

	interface Props {
		columns: readonly DataTableColumn<Row>[];
		operations: DataTableOperations;
		filters: Readonly<Record<string, string>>;
		sortColumnId: string | null;
		sortDirection: DataTableSortDirection;
		stickyHeaderOffset?: number;
		onsort: (column: DataTableColumn<Row>) => void;
		onfilter: (columnId: string, value: string) => void;
	}

	let { columns, operations, filters, sortColumnId, sortDirection, stickyHeaderOffset, onsort, onfilter }: Props = $props();
	let hasFilters = $derived(operations.kind === 'client-page' && columns.some((column) => column.filterValue));

	function stickyStyle(column: DataTableColumn<Row>): string | undefined {
		const declarations: string[] = [];
		if (stickyHeaderOffset !== undefined) declarations.push(`top: ${stickyHeaderOffset}px`);
		if (column.sticky) declarations.push(`${column.sticky.side}: ${column.sticky.offset}px`);
		return declarations.length ? declarations.join('; ') : undefined;
	}

	function headerClass(column: DataTableColumn<Row>): string {
		return [
			'px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-muted',
			stickyHeaderOffset !== undefined || column.sticky ? 'sticky bg-card z-30' : '',
			stickyHeaderOffset !== undefined ? 'top-0' : '',
			column.headerClass ?? ''
		].filter(Boolean).join(' ');
	}
</script>

<thead>
	<tr class="border-b border-default">
		{#each columns as column (column.id)}
			<DataTableHeaderCell {column} {sortColumnId} {sortDirection} className={headerClass(column)} style={stickyStyle(column)} {onsort} />
		{/each}
	</tr>
	{#if hasFilters}
		<tr class="border-b border-default">
			{#each columns as column (column.id)}
				<DataTableFilterCell {column} value={filters[column.id] ?? ''} className={headerClass(column)} style={stickyStyle(column)} {onfilter} />
			{/each}
		</tr>
	{/if}
</thead>
