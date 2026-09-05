<script lang="ts" generics="Row">
	import DataTableReady from './DataTableReady.svelte';
	import { validateDataTable } from './data-table';
	import type { DataTableOperations, DataTableReadyProps } from './types';

	type Props = Omit<DataTableReadyProps<Row>, 'operations' | 'emptyMessage' | 'stickyHeader' | 'busy'> & {
		operations?: DataTableOperations;
		emptyMessage?: string;
		stickyHeader?: boolean;
		busy?: boolean;
	};

	let { rows, columns, caption, regionLabel, getRowId, operations = { kind: 'none' }, emptyMessage = 'No records.', stickyHeader = false, busy = false }: Props = $props();
	let validation = $derived(
		validateDataTable(rows, columns, getRowId, operations, caption, regionLabel, emptyMessage)
	);
</script>

{#if validation.valid}
	<DataTableReady {rows} {columns} {caption} {regionLabel} {getRowId} {operations} {emptyMessage} {stickyHeader} {busy} />
{:else}
	<div role="alert" class="px-4 py-6 text-center text-sm text-danger">{validation.message}</div>
{/if}
