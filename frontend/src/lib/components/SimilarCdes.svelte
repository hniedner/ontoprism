<script lang="ts">
	import { resolve } from '$app/paths';
	import { similarCdes } from '$lib/api';
	import type { SimilarCde } from '$lib/types';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import SimilarityList from '$lib/components/SimilarityList.svelte';
	import { handleLatest } from '$lib/latest';

	let { publicId }: { publicId: string } = $props();

	let items = $state<SimilarCde[]>([]);
	let loaded = $state(false);
	let unavailable = $state(false);

	$effect(() => {
		loaded = false;
		unavailable = false;
		items = [];
		return handleLatest(similarCdes(publicId, 10), {
			ready: (result) => (items = result),
			failed: () => (unavailable = true),
			settled: () => (loaded = true)
		});
	});

	const links = $derived(
		items.map((item) => ({
			key: `${item.public_id}:${item.version}`,
			href: resolve('/repositories/cadsr/[id]', { id: item.public_id }),
			label: item.long_name,
			score: item.score
		}))
	);
</script>

<section class="min-w-0 rounded-xl border border-default bg-card p-4 shadow-sm">
	<h3 class="mb-3 flex items-center gap-2 text-sm font-semibold text-default">
		Similar CDEs
		<span class="rounded-full bg-subtle px-2 py-0.5 text-xs font-normal text-muted"
			>{loaded ? items.length : '…'}</span
		>
	</h3>
	{#if unavailable}
		<p class="text-sm italic text-subtle">Embeddings unavailable.</p>
	{:else if !loaded}
		<LoadingState active label="Loading similar CDEs" minHeight="4rem" />
	{:else if items.length === 0}
		<p class="text-sm italic text-subtle">None.</p>
	{:else}
		<SimilarityList items={links} />
	{/if}
</section>
