<script lang="ts" generics="Row">
	import type { DataTableColumn, DataTableSortDirection } from './types';

	let { column, sortColumnId, sortDirection, className, style, onsort }: {
		column: DataTableColumn<Row>;
		sortColumnId: string | null;
		sortDirection: DataTableSortDirection;
		className: string;
		style?: string;
		onsort: (column: DataTableColumn<Row>) => void;
	} = $props();
</script>

<th scope="col" class={className} {style} aria-sort={sortColumnId === column.id ? (sortDirection === 'asc' ? 'ascending' : 'descending') : column.sortValue ? 'none' : undefined}>
	{#if column.sortValue}
		<button type="button" class="inline-flex items-center gap-1 hover:text-default" aria-label={`Sort by ${column.label}`} onclick={() => onsort(column)}>
			{column.label} <span aria-hidden="true" class="text-subtle">{sortColumnId === column.id ? (sortDirection === 'asc' ? '↑' : '↓') : '↕'}</span>
		</button>
	{:else}{column.label}{/if}
</th>
