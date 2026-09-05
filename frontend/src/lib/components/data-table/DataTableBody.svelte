<script lang="ts" generics="Row">
	import type { DataTableColumn } from './types';

	interface Props {
		rows: readonly Row[];
		columns: readonly DataTableColumn<Row>[];
		getRowId: (row: Row) => string;
		hasActiveFilters: boolean;
		emptyMessage: string;
		filteredEmptyMessage: string;
	}

	let { rows, columns, getRowId, hasActiveFilters, emptyMessage, filteredEmptyMessage }: Props = $props();
	const stickyStyle = (column: DataTableColumn<Row>) => column.sticky ? `${column.sticky.side}: ${column.sticky.offset}px` : undefined;
	const cellClass = (column: DataTableColumn<Row>) => ['px-4 py-2.5 align-top', column.sticky ? 'sticky bg-card z-20' : '', column.cellClass ?? ''].filter(Boolean).join(' ');
</script>

<tbody>
	{#each rows as row (getRowId(row))}
		<tr class="border-b border-default/60 transition-colors hover:bg-subtle">
			{#each columns as column (column.id)}
				<td class={cellClass(column)} style={stickyStyle(column)}>{@render column.cell(row)}</td>
			{/each}
		</tr>
	{:else}
		<tr><td colspan={columns.length} class="px-4 py-6 text-center text-sm italic text-muted">{hasActiveFilters ? filteredEmptyMessage : emptyMessage}</td></tr>
	{/each}
</tbody>
