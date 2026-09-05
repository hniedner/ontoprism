<script lang="ts" generics="Row">
	import type { DataTableColumn } from './types';

	interface Props {
		rows: readonly Row[];
		columns: readonly DataTableColumn<Row>[];
		getRowId: (row: Row) => string;
		sourceRowsPresent: boolean;
		hasActiveFilters: boolean;
		emptyMessage: string;
	}

	let { rows, columns, getRowId, sourceRowsPresent, hasActiveFilters, emptyMessage }: Props = $props();
	const stickyStyle = (column: DataTableColumn<Row>) => column.sticky ? `${column.sticky.side}: ${column.sticky.offset}px` : undefined;
	const cellClass = (column: DataTableColumn<Row>) => ['border-b border-default/60 px-4 py-2.5 align-top', column.sticky ? 'sticky bg-card z-20' : ''].filter(Boolean).join(' ');
</script>

<tbody>
	{#each rows as row (getRowId(row))}
		<tr class="transition-colors hover:bg-subtle">
			{#each columns as column (column.id)}
				<td class={cellClass(column)} style={stickyStyle(column)}>{@render column.cell(row)}</td>
			{/each}
		</tr>
	{:else}
		<tr><td colspan={columns.length} class="border-b border-default/60 px-4 py-6 text-center text-sm italic text-muted">{sourceRowsPresent && hasActiveFilters ? 'No loaded rows match the page-local filters.' : emptyMessage}</td></tr>
	{/each}
</tbody>
