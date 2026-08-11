<script lang="ts">
	import BrowserGraph from '$lib/components/BrowserGraph.svelte';
	import NeighborhoodGraph from '$lib/components/NeighborhoodGraph.svelte';
	import type { Neighborhood } from '$lib/types';

	let { code, graph }: { code: string; graph: Neighborhood } = $props();
	let mode = $state<'interactive' | 'radial'>('interactive');
</script>

<section>
	<div class="mb-2 flex items-center justify-between">
		<h2 class="text-sm font-semibold text-default">Concept graph</h2>
		<div class="inline-flex overflow-hidden rounded-lg border border-default text-xs">
			{#each ['interactive', 'radial'] as option (option)}
				<button
					type="button"
					class="px-2.5 py-1 {mode === option
						? 'bg-primary-600 text-white'
						: 'text-secondary hover:bg-subtle'}"
					onclick={() => (mode = option as typeof mode)}>{option === 'interactive' ? 'Interactive' : 'Radial'}</button
				>
			{/each}
		</div>
	</div>
	{#if mode === 'interactive'}
		{#key code}<BrowserGraph {code} initial={graph} />{/key}
	{:else}
		<NeighborhoodGraph {graph} />
	{/if}
</section>
