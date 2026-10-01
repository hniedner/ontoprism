<script lang="ts">
	import { resolve } from '$app/paths';
	import RepoBrowsePage from '$lib/components/RepoBrowsePage.svelte';
	import type { PageProps } from './$types';
	import type { UberonRepositorySort, UberonSearchHit } from '$lib/types';
	import DataTable from '$lib/components/data-table/DataTable.svelte';
	import { columnFilter, gridControls } from '$lib/repository-registry';
	import type { DataTableColumn, DataTableOperations } from '$lib/components/data-table/types';

	let { data }: PageProps = $props();
	const suggestions = ['lung', 'blood vessel', 'epithelial cell', 'neuron'];
	const controls = $derived(gridControls('uberon'));
	const sortKeys = $derived(controls.sortKeys as Record<string, Partial<Record<'asc' | 'desc', UberonRepositorySort>>>);
	const sourceLabel = (hit: UberonSearchHit) => hit.source === 'cl' ? 'Cell Ontology' : 'Uberon';
	const columns = $derived<readonly DataTableColumn<UberonSearchHit>[]>([
		{ id: 'code', label: 'Code', cell: codeCell, sortable: ['asc', 'desc'], filter: columnFilter('uberon', 'code', 'Filter Uberon/CL codes'), sticky: { side: 'left', offset: 0 } },
		{ id: 'label', label: 'Name', cell: labelCell, sortable: ['asc', 'desc'], filter: columnFilter('uberon', 'label', 'Filter Uberon/CL names') },
		{ id: 'source', label: 'Source', cell: sourceCell, filter: columnFilter('uberon', 'source', 'Filter ontology sources') }
	]);
</script>

{#snippet codeCell(hit: UberonSearchHit)}<a class="font-mono text-xs" href={resolve('/repositories/uberon/[curie]', { curie: hit.code })}>{hit.code}</a>{/snippet}
{#snippet labelCell(hit: UberonSearchHit)}<a href={resolve('/repositories/uberon/[curie]', { curie: hit.code })}>{hit.label ?? '—'}</a>{/snippet}
{#snippet sourceCell(hit: UberonSearchHit)}{sourceLabel(hit)}{/snippet}

<RepoBrowsePage
	title="Uberon/CL Concepts"
	route={resolve('/repositories/uberon')}
	description="Browse the certified combined Uberon and Cell Ontology index, including named hierarchy and OWL restriction relations."
	placeholder="Search Uberon/CL concepts… e.g. lung"
	ariaLabel="Search Uberon and Cell Ontology"
	{suggestions}
	browseTitle="Browsing all Uberon/CL concepts"
	initial={data.initial}
	defaultSort={data.initial.query ? 'relevance' : 'source'}
	{sortKeys}
	filterKeys={controls.filterKeys}
	textKeys={controls.textKeys}
	countLabel={(count, mode) => `${count.toLocaleString()} ${mode === 'search' ? 'matches' : 'concepts'}`}
>
	{#snippet helpText()}
		Search labels and exact synonyms. Each result identifies whether the class is in Uberon or Cell
		Ontology within the certified combined index.
	{/snippet}
	{#snippet results(hits: UberonSearchHit[], operations: DataTableOperations, emptyMessage: string)}
		<DataTable rows={hits} {columns} caption="Uberon and Cell Ontology repository results" regionLabel="Uberon and Cell Ontology repository results" getRowId={(hit) => hit.code} {operations} {emptyMessage} stickyHeader={true} />
	{/snippet}
</RepoBrowsePage>
