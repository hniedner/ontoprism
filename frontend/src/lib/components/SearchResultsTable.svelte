<script lang="ts">
	import { resolve } from '$app/paths';
	import type { SearchHit } from '$lib/types';
	import { nextDir, sortBy, type SortDir } from '$lib/sort';

	let { hits }: { hits: SearchHit[] } = $props();

	type Col = 'code' | 'label' | 'semantic_type';
	let sortKey = $state<Col>('label');
	let sortDir = $state<SortDir>('asc');

	const value = (h: SearchHit, k: Col): string | null =>
		k === 'code' ? h.code : k === 'label' ? h.label : h.semantic_type;

	let sorted = $derived(sortBy(hits, (h) => value(h, sortKey), sortDir));

	function toggle(k: Col) {
		if (sortKey === k) sortDir = nextDir(sortDir);
		else {
			sortKey = k;
			sortDir = 'asc';
		}
	}

	const arrow = (k: Col) => (sortKey === k ? (sortDir === 'asc' ? ' ▲' : ' ▼') : '');
</script>

<table>
	<thead>
		<tr>
			<th><button type="button" onclick={() => toggle('code')}>Code{arrow('code')}</button></th>
			<th><button type="button" onclick={() => toggle('label')}>Label{arrow('label')}</button></th>
			<th
				><button type="button" onclick={() => toggle('semantic_type')}
					>Semantic type{arrow('semantic_type')}</button
				></th
			>
		</tr>
	</thead>
	<tbody>
		{#each sorted as hit (hit.code)}
			<tr>
				<td><a href={resolve('/repositories/ncit/[code]', { code: hit.code })}>{hit.code}</a></td>
				<td>{hit.label ?? '—'}</td>
				<td>{hit.semantic_type ?? '—'}</td>
			</tr>
		{/each}
	</tbody>
</table>

{#if sorted.length === 0}
	<p class="empty">No results.</p>
{/if}

<style>
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.9rem;
	}
	th,
	td {
		text-align: left;
		padding: 0.4rem 0.6rem;
		border-bottom: 1px solid #e2e2e2;
	}
	th button {
		background: none;
		border: none;
		font: inherit;
		font-weight: 600;
		cursor: pointer;
		padding: 0;
		color: inherit;
	}
	tbody tr:hover {
		background: #f6f8fa;
	}
	.empty {
		color: #666;
		font-style: italic;
	}
</style>
