<script lang="ts">
	import type { SearchHit } from '$lib/types';
	import RepoBrowsePage from '$lib/components/RepoBrowsePage.svelte';
	import SearchResultsTable from '$lib/components/SearchResultsTable.svelte';
	import type { PageProps } from './$types';
	import { resolve } from '$app/paths';
	import type { DataTableOperations } from '$lib/components/data-table/types';

	const SUGGESTIONS = ['melanoma', 'thyroid carcinoma', 'BRCA1 gene', 'tumor stage', 'lung neoplasm'];
	let { data }: PageProps = $props();

</script>

	<RepoBrowsePage
	title="NCIt Concepts"
	route={resolve('/repositories/ncit')}
	description="Browse and search NCI Thesaurus concepts. Explore the biomedical ontology hierarchy, concept roles, and semantically similar terms."
	placeholder="Search NCIt concepts… e.g. breast cancer subtypes"
	ariaLabel="Search NCIt"
	suggestions={SUGGESTIONS}
	browseTitle="Browsing all concepts"
	initial={data.initial}
	defaultSort={data.initial.query ? 'relevance' : 'source'}
	sortKeys={{ code: { asc: 'code:asc', desc: 'code:desc' }, label: { asc: 'label:asc', desc: 'label:desc' } }}
	filterKeys={{ representation_status: 'representation_status' }}
	countLabel={(n: number, mode: 'browse' | 'search') =>
		`${n.toLocaleString()} ${mode === 'search' ? 'matches' : 'concepts'}`}
>
	{#snippet helpText()}
		Search by term or synonym (e.g. <em>melanoma</em>). Click any concept to see its definition,
		hierarchy, typed roles, neighborhood graph, mapped caDSR CDEs, and embedding-based similar
		concepts.
	{/snippet}
	{#snippet results(hits: SearchHit[], operations: DataTableOperations, emptyMessage: string)}
		<SearchResultsTable {hits} {operations} {emptyMessage} />
	{/snippet}
</RepoBrowsePage>
