<script lang="ts">
	import { resolve } from '$app/paths';
	import { similarConcepts } from '$lib/api';
	import type { SimilarConcept } from '$lib/types';

	let { code }: { code: string } = $props();

	let items = $state<SimilarConcept[]>([]);
	let loaded = $state(false);
	let unavailable = $state(false);

	$effect(() => {
		loaded = false;
		unavailable = false;
		similarConcepts(code, 10)
			.then((r) => (items = r))
			.catch(() => (unavailable = true))
			.finally(() => (loaded = true));
	});
</script>

<section>
	<h3>Similar concepts <span class="count">({loaded ? items.length : '…'})</span></h3>
	{#if unavailable}
		<p class="empty">Embeddings unavailable.</p>
	{:else if loaded && items.length === 0}
		<p class="empty">None.</p>
	{:else}
		<ul class="refs">
			{#each items as c (c.code)}
				<li>
					<a href={resolve('/repositories/ncit/[code]', { code: c.code })}>{c.label ?? c.code}</a>
					<span class="score">{c.score.toFixed(2)}</span>
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
	.refs {
		list-style: none;
		padding: 0;
		margin: 0;
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
		font-size: 0.88rem;
	}
	.score {
		color: #16a34a;
		font-variant-numeric: tabular-nums;
		font-size: 0.78rem;
	}
	.empty {
		color: #888;
		font-style: italic;
		margin: 0;
	}
</style>
