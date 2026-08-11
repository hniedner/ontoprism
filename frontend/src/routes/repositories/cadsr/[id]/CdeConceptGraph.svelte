<script lang="ts">
	import { getCdeNeighborhood } from '$lib/api';
	import BrowserGraph from '$lib/components/BrowserGraph.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import type { CdeDetail, Neighborhood } from '$lib/types';

	let { cde }: { cde: CdeDetail } = $props();
	let graph = $state<Neighborhood | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let generation = 0;
	let requestController: AbortController | null = null;

	$effect(() => {
		if (!cde.public_id) return;
		generation += 1;
		requestController?.abort();
		requestController = null;
		graph = null;
		loading = false;
		error = null;
		return () => {
			generation += 1;
			requestController?.abort();
			requestController = null;
		};
	});

	async function load(): Promise<void> {
		requestController?.abort();
		const controller = new AbortController();
		requestController = controller;
		const current = ++generation;
		const publicId = cde.public_id;
		loading = true;
		error = null;
		try {
			const result = await getCdeNeighborhood(publicId, 1, undefined, controller.signal);
			if (current === generation) graph = result;
		} catch (reason) {
			if (current === generation) error = reason instanceof Error ? reason.message : String(reason);
		} finally {
			if (current === generation) {
				loading = false;
				requestController = null;
			}
		}
	}
</script>

<section class="my-6">
	<div class="mb-2 flex items-center justify-between">
		<h2 class="text-sm font-semibold text-default">Concept graph</h2>
		{#if !graph}
			<button
				type="button"
				onclick={load}
				disabled={loading || cde.concepts.length === 0}
				class="rounded-lg border border-default px-2.5 py-1 text-xs text-secondary hover:bg-subtle disabled:opacity-50"
			>
				Explore in graph
			</button>
		{/if}
	</div>
	{#if error}
		<p role="alert" class="text-sm text-danger">{error}</p>
	{:else if graph}
		<BrowserGraph code={graph.center} initial={graph} />
	{:else if loading}
		<LoadingState active label="Loading concept graph" minHeight="32rem" />
	{:else if cde.concepts.length === 0}
		<p class="text-sm italic text-subtle">No mapped NCIt concepts to graph.</p>
	{/if}
</section>
