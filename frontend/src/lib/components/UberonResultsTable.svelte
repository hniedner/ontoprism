<script lang="ts">
	import { resolve } from '$app/paths';
	import DataTable from '$lib/components/data-table/DataTable.svelte';
	import type { DataTableColumn } from '$lib/components/data-table/types';
	import type { UberonSearchHit } from '$lib/types';

	let { hits }: { hits: readonly UberonSearchHit[] } = $props();
	const sourceLabel = (hit: UberonSearchHit) => hit.source === 'cl' ? 'Cell Ontology' : 'Uberon';
	const operations = { kind: 'client-page', scopeLabel: 'Filters and sorting apply only to the Uberon/CL rows loaded on this page.' } as const;
	const columns: readonly DataTableColumn<UberonSearchHit>[] = [
		{ id: 'code', label: 'Code', cell: codeCell, sortValue: (hit) => hit.code, filterValue: (hit) => hit.code, filterAriaLabel: 'Filter loaded Uberon/CL codes' },
		{ id: 'label', label: 'Name', cell: labelCell, sortValue: (hit) => hit.label, filterValue: (hit) => hit.label, filterAriaLabel: 'Filter loaded Uberon/CL names' },
		{ id: 'source', label: 'Source', cell: sourceCell, sortValue: sourceLabel, filterValue: sourceLabel, filterAriaLabel: 'Filter loaded ontology sources' }
	];
</script>

{#snippet codeCell(hit: UberonSearchHit)}<a class="font-mono text-xs" href={resolve('/repositories/uberon/[curie]', { curie: hit.code })}>{hit.code}</a>{/snippet}
{#snippet labelCell(hit: UberonSearchHit)}<a href={resolve('/repositories/uberon/[curie]', { curie: hit.code })}>{hit.label ?? '—'}</a>{/snippet}
{#snippet sourceCell(hit: UberonSearchHit)}{sourceLabel(hit)}{/snippet}

<DataTable rows={hits} {columns} caption="Uberon and Cell Ontology results loaded on this page" regionLabel="Uberon and Cell Ontology loaded-page results" getRowId={(hit) => hit.code} {operations} />
