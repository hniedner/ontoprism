<script lang="ts">
	import RepoBrowsePage from './RepoBrowsePage.svelte';
	import type { DataTableIntent, DataTableOperations } from './data-table/types';

	let {
		filterKeys,
		initialFilters,
		intent,
		sortKeys = {}
	}: {
		filterKeys: Readonly<Record<string, string>>;
		initialFilters: Record<string, string[]>;
		intent: DataTableIntent;
		sortKeys?: Readonly<Record<string, { asc: string; desc: string }>>;
	} = $props();
</script>

{#snippet helpText()}Help{/snippet}
{#snippet results(_hits: Array<{ id: string }>, operations: DataTableOperations, _emptyMessage: string)}
	<span class="sr-only">{_hits.length} rows: {_emptyMessage}</span>
	<button type="button" onclick={() => operations.kind === 'server' && operations.onintent(intent)}>Send table intent</button>
{/snippet}

<RepoBrowsePage
	title="Repository"
	description="Repository description"
	route="/repositories/ncit"
	{helpText}
	placeholder="Search"
	ariaLabel="Search repository"
	suggestions={[]}
	browseTitle="Records"
	countLabel={(total) => `${total} records`}
	{results}
	initial={{ result: { total: 0, hits: [] }, query: '', offset: 0, size: 25, sort: 'source', filters: initialFilters }}
	defaultSort="source"
	{sortKeys}
	{filterKeys}
/>
