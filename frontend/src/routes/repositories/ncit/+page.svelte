<script lang="ts">
	import { listNcit, searchNcit } from '$lib/api';
	import type { SearchHit } from '$lib/types';
	import RepoBrowsePage from '$lib/components/RepoBrowsePage.svelte';
	import SearchResultsTable from '$lib/components/SearchResultsTable.svelte';

	const SUGGESTIONS = ['melanoma', 'thyroid carcinoma', 'BRCA1 gene', 'tumor stage', 'lung neoplasm'];
</script>

<RepoBrowsePage
	title="NCIt Concepts"
	description="Browse and search NCI Thesaurus concepts. Explore the biomedical ontology hierarchy, concept roles, and semantically similar terms."
	searchFn={searchNcit}
	listFn={listNcit}
	placeholder="Search NCIt concepts… e.g. breast cancer subtypes"
	ariaLabel="Search NCIt"
	suggestions={SUGGESTIONS}
	browseTitle="Browsing all concepts"
	countLabel={(n: number, mode: 'browse' | 'search') =>
		`${n.toLocaleString()} ${mode === 'search' ? 'matches' : 'concepts'}`}
>
	{#snippet helpText()}
		Search by term or synonym (e.g. <em>melanoma</em>). Click any concept to see its definition,
		hierarchy, typed roles, neighborhood graph, mapped caDSR CDEs, and embedding-based similar
		concepts.
	{/snippet}
	{#snippet results(hits: SearchHit[])}
		<SearchResultsTable {hits} />
	{/snippet}
</RepoBrowsePage>
