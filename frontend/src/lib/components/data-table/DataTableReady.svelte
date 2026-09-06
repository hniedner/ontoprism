<script lang="ts" generics="Row">
	import DataTableBody from './DataTableBody.svelte';
	import DataTableHead from './DataTableHead.svelte';
	import type { DataTableIntent, DataTableReadyProps } from './types';
	let { rows, columns, caption, regionLabel, getRowId, operations, emptyMessage, stickyHeader }: DataTableReadyProps<Row> = $props();
	const filters = $derived(operations.kind === 'server' ? operations.filters : {});
	const sort = $derived(operations.kind === 'server' ? operations.sort : null);
	const activeFilters = $derived(Object.entries(filters).filter(([, state]) => state.selected.length > 0));
	const busy = $derived(operations.kind === 'server' && operations.busy);
	function emit(intent: DataTableIntent): void { if (operations.kind === 'server') operations.onintent(intent); }
</script>

{#if operations.kind === 'server'}
	<div class="flex flex-wrap items-center gap-2 px-4 py-2" aria-label="Active filters">
		<span class="text-xs text-muted">Sort: {operations.activeSortLabel}</span>
		{#each activeFilters as [columnId, state] (columnId)}
			<button type="button" class="rounded bg-subtle px-2 py-1 text-xs" onclick={() => emit({ kind: 'clear-filter', columnId })} aria-label={`Clear ${columnId} filter`}>
				{columnId}: {state.selected.join(', ')} ×
			</button>
		{/each}
		{#if activeFilters.length}<button type="button" class="text-xs underline" onclick={() => emit({ kind: 'clear-filters' })}>Clear all filters</button>{/if}
		<button type="button" class="ml-auto text-xs underline" onclick={() => emit({ kind: 'reset' })}>Reset table</button>
	</div>
{/if}
<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
<div class="overflow-x-auto" role="region" aria-label={regionLabel} tabindex="0" aria-busy={busy}>
	<div class="sr-only" aria-live="polite">{rows.length.toLocaleString()} rows displayed</div>
	<table class="table-auto min-w-full border-separate border-spacing-0 text-sm">
		<caption class="sr-only">{caption}</caption>
		<DataTableHead {columns} {filters} {sort} {stickyHeader} onintent={emit} />
		<DataTableBody {rows} {columns} {getRowId} {emptyMessage} />
	</table>
</div>
