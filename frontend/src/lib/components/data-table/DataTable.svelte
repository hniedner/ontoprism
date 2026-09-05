<script lang="ts" generics="Row">
	import DataTableReady from './DataTableReady.svelte';
	import { validateDataTable } from './data-table';
	import type { DataTableOperations, DataTableReadyProps } from './types';

	type Props = Omit<DataTableReadyProps<Row>, 'operations' | 'emptyMessage' | 'stickyHeader'> & {
		operations?: DataTableOperations;
		emptyMessage?: string;
		stickyHeader?: boolean;
	};

	let { rows, columns, caption, regionLabel, getRowId, operations = { kind: 'none' }, initialSort, emptyMessage = 'No records.', stickyHeader = false }: Props = $props();
	let validation = $derived(
		validateDataTable(rows, columns, getRowId, operations, initialSort, caption, regionLabel, emptyMessage)
	);
</script>

{#if validation.valid}
	<DataTableReady {rows} {columns} {caption} {regionLabel} {getRowId} {operations} {initialSort} {emptyMessage} {stickyHeader} />
{:else}
	<div role="alert" class="px-4 py-6 text-center text-sm text-danger">{validation.message}</div>
{/if}
