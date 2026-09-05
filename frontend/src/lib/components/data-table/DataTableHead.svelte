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
		stickyHeader: boolean;
		onsort: (column: DataTableColumn<Row>) => void;
		onfilter: (columnId: string, value: string) => void;
	}

	let { columns, operations, filters, sortColumnId, sortDirection, stickyHeader, onsort, onfilter }: Props = $props();
	let hasFilters = $derived(operations.kind === 'client-page' && columns.some((column) => column.filter));

	function stickyStyle(column: DataTableColumn<Row>): string | undefined {
		const declarations: string[] = [];
		if (column.sticky) declarations.push(`${column.sticky.side}: ${column.sticky.offset}px`);
		return declarations.length ? declarations.join('; ') : undefined;
	}

	function headerClass(column: DataTableColumn<Row>): string {
		return [
			'border-b border-default px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-muted',
			column.sticky ? 'sticky bg-card z-30' : ''
		].filter(Boolean).join(' ');
	}
</script>

<thead class={stickyHeader ? 'sticky top-0 z-30 bg-card' : undefined}>
	<tr>
		{#each columns as column (column.id)}
			<DataTableHeaderCell {column} {sortColumnId} {sortDirection} className={headerClass(column)} style={stickyStyle(column)} {onsort} />
		{/each}
	</tr>
	{#if hasFilters}
		<tr>
			{#each columns as column (column.id)}
				<DataTableFilterCell {column} value={filters[column.id] ?? ''} className={headerClass(column)} style={stickyStyle(column)} {onfilter} />
			{/each}
		</tr>
	{/if}
</thead>
