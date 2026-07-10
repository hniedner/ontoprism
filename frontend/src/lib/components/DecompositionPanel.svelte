<script lang="ts">
	import { getDecomposition } from '$lib/api';
	import type { ConceptDecomposition, DecompositionConstituent } from '$lib/types';
	import DecompositionAxes from '$lib/components/DecompositionAxes.svelte';

	let { code }: { code: string } = $props();

	let data = $state<ConceptDecomposition | null>(null);
	let loaded = $state(false);
	let unavailable = $state(false);

	$effect(() => {
		loaded = false;
		unavailable = false;
		data = null;
		getDecomposition(code).then(
			(d) => (data = d),
			() => (unavailable = true)
		).finally(() => (loaded = true));
	});

	// Group constituents by axis for display (axes → their fillers), order preserved.
	const axes = $derived.by(() => {
		const order: string[] = [];
		const byAxis: Record<string, DecompositionConstituent[]> = {};
		for (const c of data?.constituents ?? []) {
			if (!byAxis[c.axis]) {
				byAxis[c.axis] = [];
				order.push(c.axis);
			}
			byAxis[c.axis].push(c);
		}
		return order.map((axis) => ({
			axis,
			label: byAxis[axis][0].axis_label ?? axis,
			items: byAxis[axis]
		}));
	});
</script>

<section class="rounded-xl border border-default bg-card p-4 shadow-sm">
	<h3 class="mb-3 flex items-center gap-2 text-sm font-semibold text-default">
		Decomposition
		{#if loaded && data?.is_legacy_precoordinated}
			<span
				class="rounded-full bg-warning-50 px-2 py-0.5 text-xs font-medium text-warning dark:bg-warning-900/30"
				title="This concept fuses several semantic axes; its atomic constituents are shown below."
				>legacy pre-coordinated</span
			>
		{/if}
	</h3>

	{#if unavailable}
		<p class="text-sm italic text-subtle">Decomposition unavailable.</p>
	{:else if !loaded}
		<p class="text-sm italic text-subtle">…</p>
	{:else if !data?.is_legacy_precoordinated}
		<p class="text-sm italic text-subtle">Not decomposed — this concept is already atomic.</p>
	{:else}
		<DecompositionAxes {axes} />
	{/if}
</section>
