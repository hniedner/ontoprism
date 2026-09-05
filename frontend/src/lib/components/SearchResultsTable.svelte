<script lang="ts">
	import { resolve } from '$app/paths';
	import type { SearchHit } from '$lib/types';
	import DataTable from '$lib/components/data-table/DataTable.svelte';
	import type { DataTableColumn } from '$lib/components/data-table/types';
	import RepresentationStatusBadge from '$lib/components/RepresentationStatusBadge.svelte';

	let { hits }: { hits: readonly SearchHit[] } = $props();
	const operations = {
		kind: 'client-page',
		scopeLabel: 'Filters and sorting apply only to the rows loaded on this page.'
	} as const;
	const columns: readonly DataTableColumn<SearchHit>[] = [
		{ id: 'code', label: 'Code', cell: codeCell, sortValue: (hit) => hit.code, filter: { kind: 'text', value: (hit) => hit.code, ariaLabel: 'Filter loaded NCIt codes' }, sticky: { side: 'left', offset: 0 } },
		{ id: 'label', label: 'Name', cell: labelCell, sortValue: (hit) => hit.label, filter: { kind: 'text', value: (hit) => hit.label, ariaLabel: 'Filter loaded NCIt names' } },
		{ id: 'semantic_type', label: 'Semantic type', cell: semanticTypeCell, sortValue: (hit) => hit.semantic_type, filter: { kind: 'categorical', value: (hit) => hit.semantic_type, ariaLabel: 'Filter loaded NCIt semantic types', emptyLabel: 'No semantic type' } },
		{ id: 'representation_status', label: 'Status', cell: statusCell, sortValue: (hit) => hit.representation_status, filter: { kind: 'categorical', value: (hit) => hit.representation_status, ariaLabel: 'Filter loaded NCIt statuses', emptyLabel: 'No status' } }
	];
</script>

{#snippet codeCell(hit: SearchHit)}
	<a href={resolve('/repositories/ncit/[code]', { code: hit.code })} class="rounded bg-subtle px-1.5 py-0.5 font-mono text-xs text-primary-600 no-underline hover:text-primary-700 dark:text-primary-400">{hit.code}</a>
{/snippet}
{#snippet labelCell(hit: SearchHit)}
	<a href={resolve('/repositories/ncit/[code]', { code: hit.code })} class="font-medium text-default no-underline hover:text-primary-600">{hit.label ?? '—'}</a>
{/snippet}
{#snippet semanticTypeCell(hit: SearchHit)}
	{#if hit.semantic_type}<span class="whitespace-nowrap text-muted">{hit.semantic_type}</span>{:else}<span class="text-muted">—</span>{/if}
{/snippet}
{#snippet statusCell(hit: SearchHit)}
	{#if hit.representation_status}<RepresentationStatusBadge status={hit.representation_status} />{:else}<span class="text-muted">—</span>{/if}
{/snippet}

<DataTable
	rows={hits}
	{columns}
	caption="NCIt results loaded on this page"
	regionLabel="NCIt loaded-page results"
	getRowId={(hit) => hit.code}
	{operations}
	stickyHeader={true}
	initialSort={{ columnId: 'label', direction: 'asc' }}
	emptyMessage="No results."
/>
