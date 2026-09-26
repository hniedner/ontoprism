<script lang="ts">
	import { getDecomposition, getConstituentEvidence } from '$lib/api';
	import type { ConceptDecomposition, DecompositionConstituent, ConstituentEvidence } from '$lib/types';
	import DecompositionAxes from '$lib/components/DecompositionAxes.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import RepresentationStatusBadge from '$lib/components/RepresentationStatusBadge.svelte';
	import { handleLatest } from '$lib/latest';
    import FillerSupportSummary from './FillerSupportSummary.svelte';

	let { code }: { code: string } = $props();

	let data = $state<ConceptDecomposition | null>(null);
	let loaded = $state(false);
	let unavailable = $state(false);
    let evidence = $state<ConstituentEvidence[] | null>(null);
    let evidenceError = $state(false);

    $effect(() => {
        evidence = null;
        evidenceError = false;
        const result = data;
        if (!result?.run_id || !result.constituents.length) return;
        const controller = new AbortController();
        return handleLatest(getConstituentEvidence(result.run_id, result.code, controller.signal), {
            ready: (rows) => {
                const matches = rows.length === result.constituents.length && result.constituents.every(c =>
                    rows.filter(r => r.run_id === result.run_id && r.concept_code === result.code && r.axis === c.axis && r.filler_code === c.filler).length === 1);
                if (matches) evidence = rows;
                else evidenceError = true;
            },
            failed: () => (evidenceError = true),
            settled: () => {}
        }, () => controller.abort());
    });

	$effect(() => {
		loaded = false;
		unavailable = false;
		data = null;
		const controller = new AbortController();
		return handleLatest(
			getDecomposition(code, undefined, controller.signal),
			{
				ready: (result) => (data = result),
				failed: () => (unavailable = true),
				settled: () => (loaded = true)
			},
			() => controller.abort()
		);
	});

	// Group constituents by axis for display (axes → their fillers), order preserved.
	const axes = $derived.by(() => {
		const order: string[] = [];
		const byAxis: Record<string, DecompositionConstituent[]> = {};
		for (const c of data?.constituents ?? []) {
			const label = c.axis_label ?? c.axis;
			const key = `${c.axis}:${label}`;
			if (!byAxis[key]) {
				byAxis[key] = [];
				order.push(key);
			}
			byAxis[key].push(c);
		}
		return order.map((key) => ({
			axis: byAxis[key][0].axis,
			label: byAxis[key][0].axis_label ?? byAxis[key][0].axis,
			items: byAxis[key]
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
	{#if loaded && data?.publication_status}
		<div class="bg-status-warning text-status-warning mb-4 rounded border border-warning-200 p-3 text-sm dark:border-warning-800">
			<p class="font-semibold">{data.publication_notice}</p>
			<p class="mt-1 font-medium">{data.outcome}</p>
			<p class="mt-1">{data.outcome_reason}</p>
			{#each data.review_flags ?? [] as flag (flag.kind + flag.reason)}
				<p class="mt-1"><strong>{flag.kind}:</strong> {flag.reason}</p>
			{/each}
		</div>
	{/if}

	{#if unavailable}
		<p class="text-sm italic text-subtle">Decomposition unavailable.</p>
	{:else if !loaded}
		<LoadingState active label="Loading decomposition" minHeight="4rem" />
	{:else if !data?.is_legacy_precoordinated}
		<p class="text-sm italic text-subtle">No published decomposition is available.</p>
	{:else}
        <FillerSupportSummary {evidence} failed={evidenceError} runId={data?.run_id ?? null} />
		<DecompositionAxes {axes} {evidence} publicationStatus={data?.publication_status ?? null} />
	{/if}
</section>
