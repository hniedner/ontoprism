<script lang="ts" generics="Row">
	import type { DataTableColumn, DataTableIntent, DataTableSortState } from './types';
	let { column, sort, className, style, onintent }: { column: DataTableColumn<Row>; sort: DataTableSortState | null; className: string; style?: string; onintent: (intent: DataTableIntent) => void } = $props();
	const sortable = $derived(Boolean(column.sortable?.length));
	const activeDirection = $derived(sort?.key === column.id ? sort.direction : null);
	const ariaSort = $derived(activeDirection === null ? (sortable ? 'none' : undefined) : activeDirection === 'asc' ? 'ascending' : 'descending');
	const indicator = $derived(activeDirection === 'asc' ? '↑' : activeDirection === 'desc' ? '↓' : column.sortable?.length === 1 ? (column.sortable[0] === 'asc' ? '↑' : '↓') : '↕');
	function nextSort(): void {
		const directions = column.sortable ?? [];
		const current = sort?.key === column.id ? directions.indexOf(sort.direction) : -1;
		if (directions.length === 1 && current === 0) {
			onintent({ kind: 'reset' });
			return;
		}
		const direction = directions[current < 0 ? 0 : (current + 1) % directions.length];
		if (direction) onintent({ kind: 'sort', sort: { key: column.id, direction } });
	}
</script>
<th scope="col" class={className} {style} aria-sort={ariaSort}>
	{#if sortable}<button type="button" class="inline-flex items-center gap-1 hover:text-default" aria-label={`Sort by ${column.label}`} onclick={nextSort}>{column.label} <span aria-hidden="true" class="text-subtle">{indicator}</span></button>{:else}{column.label}{/if}
</th>
