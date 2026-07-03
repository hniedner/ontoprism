<script lang="ts">
	import { resolve } from '$app/paths';
	import type { Relationship } from '$lib/types';

	let { title, items }: { title: string; items: Relationship[] } = $props();
</script>

<section>
	<h3>{title} <span class="count">({items.length})</span></h3>
	{#if items.length === 0}
		<p class="empty">None.</p>
	{:else}
		<ul>
			{#each items as rel (rel.relation + rel.target.code)}
				<li>
					<span class="rel">{rel.relation_label ?? rel.relation}</span>
					<span class="arrow">→</span>
					<a href={resolve('/repositories/ncit/[code]', { code: rel.target.code })}
						>{rel.target.label ?? rel.target.code}</a
					>
					<span class="code">{rel.target.code}</span>
				</li>
			{/each}
		</ul>
	{/if}
</section>

<style>
	h3 {
		font-size: 0.95rem;
		margin: 0 0 0.4rem;
	}
	.count {
		color: #888;
		font-weight: 400;
	}
	ul {
		list-style: none;
		padding: 0;
		margin: 0;
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
	}
	li {
		display: flex;
		align-items: baseline;
		gap: 0.4rem;
		font-size: 0.88rem;
	}
	.rel {
		color: #2563eb;
		font-family: ui-monospace, monospace;
		font-size: 0.8rem;
	}
	.arrow {
		color: #999;
	}
	.code {
		color: #999;
		font-size: 0.75rem;
	}
	.empty {
		color: #888;
		font-style: italic;
		margin: 0;
	}
</style>
