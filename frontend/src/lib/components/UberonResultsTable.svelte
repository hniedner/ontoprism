<script lang="ts">
	import { resolve } from '$app/paths';
	import DataTable from '$lib/components/data-table/DataTable.svelte';
	import type { DataTableColumn, DataTableOperations } from '$lib/components/data-table/types';
	import type { UberonSearchHit } from '$lib/types';

	let { hits, operations = { kind: 'none' } }: { hits: readonly UberonSearchHit[]; operations?: DataTableOperations } = $props();
	const sourceLabel = (hit: UberonSearchHit) => hit.source === 'cl' ? 'Cell Ontology' : 'Uberon';
	const interactive = $derived(operations.kind === 'server');
	let columns = $derived.by((): readonly DataTableColumn<UberonSearchHit>[] => [
		{ id: 'code', label: 'Code', cell: codeCell, sortable: interactive ? ['asc', 'desc'] : undefined, sticky: { side: 'left', offset: 0 } },
		{ id: 'label', label: 'Name', cell: labelCell, sortable: interactive ? ['asc', 'desc'] : undefined },
		{ id: 'source', label: 'Source', cell: sourceCell, filter: interactive ? { kind: 'categorical', ariaLabel: 'Filter ontology sources', options: [{ value: 'uberon', label: 'Uberon' }, { value: 'cl', label: 'Cell Ontology' }] } : undefined }
	]);
</script>

{#snippet codeCell(hit: UberonSearchHit)}<a class="font-mono text-xs" href={resolve('/repositories/uberon/[curie]', { curie: hit.code })}>{hit.code}</a>{/snippet}
{#snippet labelCell(hit: UberonSearchHit)}<a href={resolve('/repositories/uberon/[curie]', { curie: hit.code })}>{hit.label ?? '—'}</a>{/snippet}
{#snippet sourceCell(hit: UberonSearchHit)}{sourceLabel(hit)}{/snippet}

<DataTable rows={hits} {columns} caption="Uberon and Cell Ontology repository results" regionLabel="Uberon and Cell Ontology repository results" getRowId={(hit) => hit.code} {operations} stickyHeader={true} />
