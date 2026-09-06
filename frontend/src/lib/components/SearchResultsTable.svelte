<script lang="ts">
	import { resolve } from '$app/paths';
	import type { SearchHit } from '$lib/types';
	import DataTable from '$lib/components/data-table/DataTable.svelte';
	import type { DataTableColumn, DataTableOperations } from '$lib/components/data-table/types';
	import RepresentationStatusBadge from '$lib/components/RepresentationStatusBadge.svelte';

	let { hits, operations = { kind: 'none' }, emptyMessage = 'No results.' }: { hits: readonly SearchHit[]; operations?: DataTableOperations; emptyMessage?: string } = $props();
	const interactive = $derived(operations.kind === 'server');
	let columns = $derived.by((): readonly DataTableColumn<SearchHit>[] => [
		{ id: 'code', label: 'Code', cell: codeCell, sortable: interactive ? ['asc', 'desc'] : undefined, sticky: { side: 'left', offset: 0 } },
		{ id: 'label', label: 'Name', cell: labelCell, sortable: interactive ? ['asc', 'desc'] : undefined },
		{ id: 'semantic_type', label: 'Semantic type', cell: semanticTypeCell },
		{ id: 'representation_status', label: 'Status', cell: statusCell, filter: interactive ? { kind: 'categorical', ariaLabel: 'Filter NCIt representation status', options: [{ value: 'legacy-precoordinated', label: 'Legacy pre-coordinated' }] } : undefined }
	]);
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
	caption="NCIt repository results"
	regionLabel="NCIt repository results"
	getRowId={(hit) => hit.code}
	{operations}
	stickyHeader={true}
	{emptyMessage}
/>
