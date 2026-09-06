<script lang="ts">
	import { resolve } from '$app/paths';
	import RepoBrowsePage from '$lib/components/RepoBrowsePage.svelte';
	import UberonResultsTable from '$lib/components/UberonResultsTable.svelte';
	import type { PageProps } from './$types';
	import type { UberonSearchHit } from '$lib/types';
	import type { DataTableOperations } from '$lib/components/data-table/types';

	let { data }: PageProps = $props();
	const suggestions = ['lung', 'blood vessel', 'epithelial cell', 'neuron'];

</script>

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
	sortKeys={{ code: { asc: 'code:asc', desc: 'code:desc' }, label: { asc: 'label:asc', desc: 'label:desc' } }}
	filterKeys={{ source: 'source' }}
	countLabel={(count, mode) => `${count.toLocaleString()} ${mode === 'search' ? 'matches' : 'concepts'}`}
>
	{#snippet helpText()}
		Search labels and exact synonyms. Each result identifies whether the class is in Uberon or Cell
		Ontology within the certified combined index.
	{/snippet}
	{#snippet results(hits: UberonSearchHit[], operations: DataTableOperations, emptyMessage: string)}
		<UberonResultsTable {hits} {operations} {emptyMessage} />
	{/snippet}
</RepoBrowsePage>
