<script lang="ts" generics="Row">
	import type { DataTableColumn, DataTableIntent, DataTableSortState } from './types';
	let { column, sort, className, style, onintent }: { column: DataTableColumn<Row>; sort: DataTableSortState | null; className: string; style?: string; onintent: (intent: DataTableIntent) => void } = $props();
	function nextSort(): void { onintent({ kind: 'sort', sort: { key: column.id, direction: sort?.key === column.id && sort.direction === 'asc' ? 'desc' : 'asc' } }); }
</script>
<th scope="col" class={className} {style} aria-sort={sort?.key === column.id ? (sort.direction === 'asc' ? 'ascending' : 'descending') : column.sortable ? 'none' : undefined}>
	{#if column.sortable}<button type="button" class="inline-flex items-center gap-1 hover:text-default" aria-label={`Sort by ${column.label}`} onclick={nextSort}>{column.label} <span aria-hidden="true" class="text-subtle">{sort?.key === column.id ? (sort.direction === 'asc' ? '↑' : '↓') : '↕'}</span></button>{:else}{column.label}{/if}
</th>
