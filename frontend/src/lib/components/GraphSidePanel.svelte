<script lang="ts">
	import {
		KIND_COLOR,
		type AnalyticsSummary,
		type NodeAttrs
	} from '$lib/graph/neighborhood-graph';
	import GraphNodeCard from '$lib/components/GraphNodeCard.svelte';
	import type { SvelteSet } from 'svelte/reactivity';

	interface Props {
		selected: NodeAttrs | null;
		expanding: boolean;
		stats: AnalyticsSummary;
		nodeCount: number;
		edgeCount: number;
		semanticTypes: string[];
		hiddenTypes: SvelteSet<string>;
		onexpand: (code: string) => void;
		onopen: (code: string) => void;
		onfocus: (code: string) => void;
		ontoggletype: (t: string) => void;
	}

	let {
		selected,
		expanding,
		stats,
		nodeCount,
		edgeCount,
		semanticTypes,
		hiddenTypes,
		onexpand,
		onopen,
		onfocus,
		ontoggletype
	}: Props = $props();

	const hasBridges = $derived(
		stats.topByBetweenness.length > 0 && stats.topByBetweenness[0].betweenness > 0
	);
</script>

{#snippet rankedList(items: { code: string; label: string; value: string }[])}
	<ul class="flex flex-col gap-1 text-xs">
		{#each items as n (n.code)}
			<li class="flex items-center justify-between gap-2">
				<button
					type="button"
					class="min-w-0 truncate text-left text-secondary hover:text-primary-600"
					onclick={() => onfocus(n.code)}>{n.label}</button
				>
				<span class="shrink-0 tabular-nums text-subtle">{n.value}</span>
			</li>
		{/each}
	</ul>
{/snippet}

<div class="w-64 shrink-0 overflow-y-auto border-l border-default p-3 text-sm">
	<GraphNodeCard {selected} {expanding} {onexpand} {onopen} />

	<div class="border-t border-default pt-3">
		<h5 class="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Network</h5>
		<dl class="grid grid-cols-2 gap-1 text-xs text-muted">
			<dt>Nodes</dt>
			<dd class="text-right text-default">{nodeCount}</dd>
			<dt>Edges</dt>
			<dd class="text-right text-default">{edgeCount}</dd>
			<dt>Communities</dt>
			<dd class="text-right text-default">{stats.communityCount}</dd>
		</dl>
		{#if stats.topByDegree.length}
			<h5 class="mb-1 mt-3 text-xs font-semibold uppercase tracking-wide text-muted">
				Most connected
			</h5>
			{@render rankedList(
				stats.topByDegree.map((n) => ({ code: n.code, label: n.label, value: String(n.degree) }))
			)}
		{/if}
		{#if hasBridges}
			<h5 class="mb-1 mt-3 text-xs font-semibold uppercase tracking-wide text-muted">
				Key bridges
			</h5>
			{@render rankedList(
				stats.topByBetweenness
					.filter((n) => n.betweenness > 0)
					.map((n) => ({ code: n.code, label: n.label, value: n.betweenness.toFixed(2) }))
			)}
		{/if}
	</div>

	{#if semanticTypes.length > 1}
		<div class="mt-3 border-t border-default pt-3">
			<h5 class="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Filter by type</h5>
			<div class="flex flex-wrap gap-1.5">
				{#each semanticTypes as t (t)}
					<button
						type="button"
						class="rounded-full px-2 py-0.5 text-xs {hiddenTypes.has(t)
							? 'bg-subtle text-subtle line-through'
							: 'bg-primary-50 text-primary-700 dark:bg-primary-900/30 dark:text-primary-300'}"
						onclick={() => ontoggletype(t)}
						title={hiddenTypes.has(t) ? 'Show' : 'Hide'}>{t}</button
					>
				{/each}
			</div>
		</div>
	{/if}

	<div class="mt-3 border-t border-default pt-3">
		<h5 class="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Edge types</h5>
		<ul class="flex flex-col gap-1 text-xs text-muted">
			{#each Object.entries(KIND_COLOR) as [kind, color] (kind)}
				<li class="flex items-center gap-2">
					<span class="inline-block h-2.5 w-4 rounded-sm" style:background={color}></span>
					{kind}
				</li>
			{/each}
		</ul>
	</div>
</div>
