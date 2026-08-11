<script lang="ts">
	import { resolve } from '$app/paths';
	import { similarConcepts } from '$lib/api';
	import type { SimilarConcept } from '$lib/types';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import SimilarityList from '$lib/components/SimilarityList.svelte';
	import { handleLatest } from '$lib/latest';

	let { code }: { code: string } = $props();

	let items = $state<SimilarConcept[]>([]);
	let loaded = $state(false);
	let unavailable = $state(false);

	$effect(() => {
		loaded = false;
		unavailable = false;
		items = [];
		return handleLatest(similarConcepts(code, 10), {
			ready: (result) => (items = result),
			failed: () => (unavailable = true),
			settled: () => (loaded = true)
		});
	});

	const links = $derived(
		items.map((item) => ({
			key: item.code,
			href: resolve('/repositories/ncit/[code]', { code: item.code }),
			label: item.label ?? item.code,
			score: item.score
		}))
	);
</script>

<section class="rounded-xl border border-default bg-card p-4 shadow-sm">
	<h3 class="mb-3 flex items-center gap-2 text-sm font-semibold text-default">
		Similar concepts
		<span class="rounded-full bg-subtle px-2 py-0.5 text-xs font-normal text-muted"
			>{loaded ? items.length : '…'}</span
		>
	</h3>
	{#if unavailable}
		<p class="text-sm italic text-subtle">Embeddings unavailable.</p>
	{:else if !loaded}
		<LoadingState active label="Loading similar concepts" minHeight="4rem" />
	{:else if items.length === 0}
		<p class="text-sm italic text-subtle">None.</p>
	{:else}
		<SimilarityList items={links} />
	{/if}
</section>
