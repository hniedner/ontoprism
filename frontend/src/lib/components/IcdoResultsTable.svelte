<script lang="ts">
	import { resolve } from '$app/paths';
	import { icdoCodeSegment } from '$lib/api';
	import DataTable from '$lib/components/data-table/DataTable.svelte';
	import type { DataTableColumn } from '$lib/components/data-table/types';
	import type { IcdoDataset } from '$lib/icdo-routes';
	import type { IcdoRecord } from '$lib/types';

	let { dataset, hits }: { dataset: IcdoDataset; hits: readonly IcdoRecord[] } = $props();
	const operations = { kind: 'client-page', scopeLabel: 'Filters and sorting apply only to the ICD-O records loaded on this page.' } as const;
	const columns: readonly DataTableColumn<IcdoRecord>[] = [
		{ id: 'code', label: 'Code', cell: codeCell, sortValue: (hit) => hit.code, filter: { value: (hit) => hit.code, ariaLabel: 'Filter loaded ICD-O codes' }, sticky: { side: 'left', offset: 0 } },
		{ id: 'preferred', label: 'Preferred/category term', cell: preferredCell, sortValue: (hit) => hit.preferred, filter: { value: (hit) => hit.preferred, ariaLabel: 'Filter loaded ICD-O preferred terms' } },
		{ id: 'level', label: 'Level', cell: levelCell, sortValue: (hit) => hit.level, filter: { value: (hit) => hit.level, ariaLabel: 'Filter loaded ICD-O levels' } }
	];
</script>

{#snippet codeCell(hit: IcdoRecord)}
	<a class="font-mono text-xs" href={resolve('/repositories/icdo/[edition]/[axis]/[code]', { edition: dataset.edition, axis: dataset.axis, code: icdoCodeSegment(hit.code) })}>{hit.code}</a>
{/snippet}
{#snippet preferredCell(hit: IcdoRecord)}{hit.preferred ?? 'No preferred term supplied'}{/snippet}
{#snippet levelCell(hit: IcdoRecord)}{hit.level}{/snippet}

<DataTable rows={hits} {columns} caption={`ICD-O ${dataset.edition} ${dataset.axis} records loaded on this page`} regionLabel="ICD-O loaded-page results" getRowId={(hit) => hit.code} {operations} stickyHeader={true} />
