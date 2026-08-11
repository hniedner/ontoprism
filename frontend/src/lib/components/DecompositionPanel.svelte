<script lang="ts">
	import { getDecomposition } from '$lib/api';
	import type { ConceptDecomposition, DecompositionConstituent } from '$lib/types';
	import DecompositionAxes from '$lib/components/DecompositionAxes.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import RepresentationStatusBadge from '$lib/components/RepresentationStatusBadge.svelte';

	let { code }: { code: string } = $props();

	let data = $state<ConceptDecomposition | null>(null);
	let loaded = $state(false);
	let unavailable = $state(false);

	$effect(() => {
		let cancelled = false;
		loaded = false;
		unavailable = false;
		data = null;
		getDecomposition(code).then(
			(result) => {
				if (!cancelled) data = result;
			},
			() => {
				if (!cancelled) unavailable = true;
			}
		).finally(() => {
			if (!cancelled) loaded = true;
		});
		return () => {
			cancelled = true;
		};
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
			<RepresentationStatusBadge status="legacy-precoordinated" />
		{/if}
	</h3>

	{#if unavailable}
		<p class="text-sm italic text-subtle">Decomposition unavailable.</p>
	{:else if !loaded}
		<LoadingState active label="Loading decomposition" minHeight="4rem" />
	{:else if !data?.is_legacy_precoordinated}
		<p class="text-sm italic text-subtle">No published decomposition is available.</p>
	{:else}
		<DecompositionAxes {axes} />
	{/if}
</section>
