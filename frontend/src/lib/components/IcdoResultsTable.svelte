<script lang="ts">
	import { resolve } from '$app/paths';
	import { icdoCodeSegment } from '$lib/api';
	import DataTable from '$lib/components/data-table/DataTable.svelte';
	import type { DataTableColumn, DataTableOperations } from '$lib/components/data-table/types';
	import type { IcdoDataset } from '$lib/icdo-routes';
	import type { IcdoRecord } from '$lib/types';

	let { dataset, hits, operations = { kind: 'none' } }: { dataset: IcdoDataset; hits: readonly IcdoRecord[]; operations?: DataTableOperations } = $props();
	const interactive = $derived(operations.kind === 'server');
	let columns = $derived.by((): readonly DataTableColumn<IcdoRecord>[] => [
		{ id: 'code', label: 'Code', cell: codeCell, sortable: interactive ? ['asc', 'desc'] : undefined, sticky: { side: 'left', offset: 0 } },
		{ id: 'preferred', label: 'Preferred/category term', cell: preferredCell, sortable: interactive ? ['asc', 'desc'] : undefined },
		{ id: 'level', label: 'Level', cell: levelCell, filter: interactive ? { kind: 'categorical', ariaLabel: 'Filter ICD-O levels', options: (dataset.axis === 'morphology' ? ['morphology'] : ['category', 'leaf']).map((value) => ({ value, label: value })) } : undefined },
		{ id: 'behaviour', label: 'Behaviour', cell: behaviourCell, filter: interactive && dataset.axis === 'morphology' ? { kind: 'categorical', ariaLabel: 'Filter ICD-O behaviours', options: Array.from({ length: 10 }, (_, value) => ({ value: String(value), label: String(value) })) } : undefined },
		{ id: 'specificity', label: 'Specificity', cell: specificityCell }
	]);
</script>

{#snippet codeCell(hit: IcdoRecord)}
	<a class="font-mono text-xs" href={resolve('/repositories/icdo/[edition]/[axis]/[code]', { edition: dataset.edition, axis: dataset.axis, code: icdoCodeSegment(hit.code) })}>{hit.code}</a>
{/snippet}
{#snippet preferredCell(hit: IcdoRecord)}{hit.preferred ?? 'No preferred term supplied'}{/snippet}
{#snippet levelCell(hit: IcdoRecord)}{hit.level}{/snippet}
{#snippet behaviourCell(hit: IcdoRecord)}{hit.behaviour ?? '—'}{/snippet}
{#snippet specificityCell(hit: IcdoRecord)}{hit.specificity ?? '—'}{/snippet}

<DataTable rows={hits} {columns} caption={`ICD-O ${dataset.edition} ${dataset.axis} repository records`} regionLabel="ICD-O repository results" getRowId={(hit) => hit.code} {operations} stickyHeader={true} />
