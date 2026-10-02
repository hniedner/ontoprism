<script lang="ts">
	import { resolve } from '$app/paths';
	import PubMedResultsTable from '$lib/components/PubMedResultsTable.svelte';
	import RepoBrowsePage from '$lib/components/RepoBrowsePage.svelte';
	import type { DataTableOperations } from '$lib/components/data-table/types';
	import { gridCapabilities, gridControls } from '$lib/repository-registry';
	import type { PubMedArticleSummary } from '$lib/types';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
	const controls = gridControls('pubmed');
	const dateSort = gridCapabilities('pubmed').sorts.search.includes('pub_date');
	const response = $derived(data.result.state === 'ready' ? data.result.data : null);
	const remote = $derived.by(() => data.result.state === 'error'
		? {
				service: 'NCBI PubMed' as const,
				state: 'error' as const,
				error: { remoteState: data.result.remoteState, message: data.result.message }
			}
		: {
				service: 'NCBI PubMed' as const,
				state: data.result.state,
				error: null
			});
</script>

<RepoBrowsePage
	title="PubMed"
	route={resolve('/repositories/pubmed')}
	kind="remote-live-service"
	description="Search biomedical literature through NCBI PubMed. Results are retrieved live from the NCBI E-utilities API."
	placeholder="Search PubMed… e.g. melanoma immunotherapy"
	ariaLabel="Search PubMed"
	suggestions={['melanoma immunotherapy', 'BRCA1 breast cancer', 'CRISPR gene therapy', 'single cell RNA sequencing']}
	browseTitle="PubMed articles"
	initial={{ result: { total: response?.total ?? 0, hits: response?.articles ?? [] }, query: data.query, offset: data.offset, size: data.size, sort: String(data.sort), filters: {} }}
	defaultSort="relevance"
	sortKeys={dateSort ? { date: { desc: 'pub_date' } } : {}}
	filterKeys={controls.filterKeys}
	textKeys={controls.textKeys}
	{remote}
	navigationTotal={Math.min(response?.total ?? 0, 10_000)}
	countLabel={(n: number) => `${n.toLocaleString()} articles`}
	noMatchesLabel={(query: string) => `No articles matched “${query}”.`}
>
	{#snippet helpText()}
		Search PubMed by keywords, author, title, or MeSH terms. Queries use the standard PubMed search syntax. Click an article to view its abstract and metadata.
	{/snippet}
	{#snippet instruction()}
		<p class="text-sm text-muted">Enter a query above to search PubMed.</p>
	{/snippet}
	{#snippet results(articles: PubMedArticleSummary[], operations: DataTableOperations, emptyMessage: string)}
		<PubMedResultsTable {articles} {operations} {emptyMessage} />
	{/snippet}
</RepoBrowsePage>
