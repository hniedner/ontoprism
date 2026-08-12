<script lang="ts">
	import type { ResolvedPathname } from '$app/types';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import SimilarityList from '$lib/components/SimilarityList.svelte';
	import { handleLatest } from '$lib/latest';

	export interface SimilarityLink {
		key: string;
		href: ResolvedPathname;
		label: string;
		score: number;
	}

	let {
		requestKey,
		title,
		loadingLabel,
		load
	}: {
		requestKey: string;
		title: string;
		loadingLabel: string;
		load: (requestKey: string, signal: AbortSignal) => Promise<SimilarityLink[]>;
	} = $props();

	let items = $state<SimilarityLink[]>([]);
	let loaded = $state(false);
	let unavailable = $state(false);

	$effect(() => {
		loaded = false;
		unavailable = false;
		items = [];
		const controller = new AbortController();
		return handleLatest(
			load(requestKey, controller.signal),
			{
				ready: (result) => (items = result),
				failed: () => (unavailable = true),
				settled: () => (loaded = true)
			},
			() => controller.abort()
		);
	});
</script>

<section class="min-w-0 rounded-xl border border-default bg-card p-4 shadow-sm">
	<h3 class="mb-3 flex items-center gap-2 text-sm font-semibold text-default">
		{title}
		<span class="rounded-full bg-subtle px-2 py-0.5 text-xs font-normal text-muted"
			>{loaded ? items.length : '…'}</span
		>
	</h3>
	{#if unavailable}
		<p class="text-sm italic text-subtle">Embeddings unavailable.</p>
	{:else if !loaded}
		<LoadingState active label={loadingLabel} minHeight="4rem" />
	{:else if items.length === 0}
		<p class="text-sm italic text-subtle">None.</p>
	{:else}
		<SimilarityList {items} />
	{/if}
</section>
