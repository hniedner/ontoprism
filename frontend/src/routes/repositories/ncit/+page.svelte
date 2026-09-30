<script lang="ts">
	import type { SearchHit } from '$lib/types';
	import RepoBrowsePage from '$lib/components/RepoBrowsePage.svelte';
	import DataTable from '$lib/components/data-table/DataTable.svelte';
	import RepresentationStatusBadge from '$lib/components/RepresentationStatusBadge.svelte';
	import { gridControls, columnFilter } from '$lib/repository-registry';
	import type { PageProps } from './$types';
	import { resolve } from '$app/paths';
	import type { DataTableOperations, DataTableColumn } from '$lib/components/data-table/types';

	const SUGGESTIONS = ['melanoma', 'thyroid carcinoma', 'BRCA1 gene', 'tumor stage', 'lung neoplasm'];
	let { data }: PageProps = $props();
	const controls = gridControls('ncit');
	const columns: readonly DataTableColumn<SearchHit>[] = [
		{ id: 'code', label: 'Code', cell: codeCell, sortable: Object.keys(controls.sortKeys.code) as ('asc' | 'desc')[], filter: columnFilter('ncit', 'code', 'Filter NCIt codes'), sticky: { side: 'left', offset: 0 } },
		{ id: 'label', label: 'Name', cell: labelCell, sortable: Object.keys(controls.sortKeys.label) as ('asc' | 'desc')[], filter: columnFilter('ncit', 'label', 'Filter NCIt names') },
		{ id: 'semantic_type', label: 'Semantic type', cell: semanticTypeCell },
		{ id: 'representation_status', label: 'Status', cell: statusCell, filter: columnFilter('ncit', 'representation_status', 'Filter NCIt representation status') }
	];

</script>

{#snippet codeCell(hit: SearchHit)}
	<a href={resolve('/repositories/ncit/[code]', { code: hit.code })} class="rounded bg-subtle px-1.5 py-0.5 font-mono text-xs text-primary-600 no-underline hover:text-primary-700 dark:text-primary-400">{hit.code}</a>
{/snippet}
{#snippet labelCell(hit: SearchHit)}
	<a href={resolve('/repositories/ncit/[code]', { code: hit.code })} class="font-medium text-default no-underline hover:text-primary-600">{hit.label ?? '—'}</a>
{/snippet}
{#snippet semanticTypeCell(hit: SearchHit)}
	<span class="whitespace-nowrap text-muted">{hit.semantic_type ?? '—'}</span>
{/snippet}
{#snippet statusCell(hit: SearchHit)}
	{#if hit.representation_status}<RepresentationStatusBadge status={hit.representation_status} />{:else}<span class="text-muted">—</span>{/if}
{/snippet}

<div class="mb-4 flex justify-end">
	<a href={resolve('/repositories/ncit/progress')} class="text-sm font-medium text-primary-700">Publication progress</a>
</div>

	<RepoBrowsePage
	title="NCIt Concepts"
	route={resolve('/repositories/ncit')}
	description="Browse and search NCI Thesaurus concepts. Explore the biomedical ontology hierarchy, concept roles, and semantically similar terms."
	placeholder="Search NCIt concepts… e.g. breast cancer subtypes"
	ariaLabel="Search NCIt"
	suggestions={SUGGESTIONS}
	browseTitle="Browsing all concepts"
	initial={{ ...data.initial, sort: String(data.initial.sort) }}
	defaultSort={data.initial.query ? 'relevance' : 'source'}
	sortKeys={controls.sortKeys}
	filterKeys={controls.filterKeys}
	textKeys={controls.textKeys}
	countLabel={(n: number, mode: 'browse' | 'search') =>
		`${n.toLocaleString()} ${mode === 'search' ? 'matches' : 'concepts'}`}
>
	{#snippet helpText()}
		Search by term or synonym (e.g. <em>melanoma</em>). Click any concept to see its definition,
		hierarchy, typed roles, neighborhood graph, mapped caDSR CDEs, and embedding-based similar
		concepts.
	{/snippet}
	{#snippet results(hits: SearchHit[], operations: DataTableOperations, emptyMessage: string)}
		<DataTable rows={hits} {columns} caption="NCIt repository results" regionLabel="NCIt repository results" getRowId={(hit) => hit.code} {operations} stickyHeader={true} {emptyMessage} />
	{/snippet}
</RepoBrowsePage>
