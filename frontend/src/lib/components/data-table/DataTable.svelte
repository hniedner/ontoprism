<script lang="ts" generics="Row">
	import DataTableReady from './DataTableReady.svelte';
	import { validateDataTable } from './data-table';
	import type { DataTableColumn, DataTableInitialSort, DataTableOperations, DataTableState } from './types';

	interface Props {
		rows: readonly Row[];
		columns: readonly DataTableColumn<Row>[];
		caption: string;
		regionLabel: string;
		getRowId: (row: Row) => string;
		operations?: DataTableOperations;
		initialSort?: DataTableInitialSort;
		state?: DataTableState;
		emptyMessage?: string;
		filteredEmptyMessage?: string;
		stickyHeaderOffset?: number;
	}

	let { rows, columns, caption, regionLabel, getRowId, operations = { kind: 'none' }, initialSort, state: presentationState = { kind: 'ready' }, emptyMessage = 'No records.', filteredEmptyMessage = 'No loaded rows match the page-local filters.', stickyHeaderOffset }: Props = $props();
	let validated = $derived.by(() => {
		validateDataTable(rows, columns, getRowId, operations, initialSort, stickyHeaderOffset);
		return true;
	});
</script>

{#if validated}
	{#if presentationState.kind === 'error'}
		<div role="alert" class="px-4 py-6 text-center text-sm text-danger">{presentationState.message}</div>
	{:else if presentationState.kind === 'loading'}
		<p role="status" class="px-4 py-6 text-center text-sm text-muted">{presentationState.label ?? 'Loading records'}</p>
	{:else}
		<DataTableReady {rows} {columns} {caption} {regionLabel} {getRowId} {operations} {initialSort} {emptyMessage} {filteredEmptyMessage} {stickyHeaderOffset} />
	{/if}
{/if}
