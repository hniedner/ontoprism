<script lang="ts">
	import { resolve } from '$app/paths';
	import type { DecompositionConstituent } from '$lib/types';
	import { SvelteMap } from 'svelte/reactivity';

	interface AxisGroup {
		axis: string;
		label: string;
		items: DecompositionConstituent[];
	}

	let { axes }: { axes: AxisGroup[] } = $props();

	interface DisplayConstituent {
		axisLabel: string;
		item: DecompositionConstituent;
	}

	function normalizedBlocks() {
		const blocks = new SvelteMap<string, DisplayConstituent[]>();
		for (const group of axes) {
			for (const item of group.items) {
				const key = item.normalized_group_id ?? `outside-policy:${group.label}:${item.axis}`;
				blocks.set(key, [...(blocks.get(key) ?? []), { axisLabel: group.label, item }]);
			}
		}
		return [...blocks.values()];
	}
</script>

{#snippet constituent(display: DisplayConstituent, showAxis: boolean)}
	{@const c = display.item}
	<li class="flex items-center gap-2 text-sm">
		{#if showAxis}
			<span class="font-mono text-xs uppercase tracking-wide text-muted">{display.axisLabel}</span>
		{/if}
		<a
			href={resolve('/repositories/ncit/[code]', { code: c.filler })}
			class="min-w-0 truncate text-secondary no-underline hover:text-primary-600"
			>{c.filler_label ?? c.filler}</a
		>
		<span class="font-mono text-xs text-subtle">{c.filler}</span>
		{#if c.most_specific}
			<span
				class="shrink-0 rounded bg-subtle px-1.5 py-0.5 text-xs text-muted"
				title="Chosen as the most-specific filler over its ancestors">leaf</span
			>
		{/if}
		{#if c.source_group_ids.length}
			<span class="text-xs text-subtle">Source groups: {c.source_group_ids.join(', ')}</span>
		{/if}
		{#if c.axis_ambiguous}
			<span class="text-xs text-subtle">Ambiguous axis</span>
		{/if}
	</li>
{/snippet}

{#snippet normalizedBlock(block: DisplayConstituent[])}
	{@const label = block[0].item.normalized_group_label}
	<div role="group" aria-label={label ?? block[0].axisLabel}>
		<div class="text-xs font-medium text-muted">{label ?? block[0].axisLabel}</div>
	<ul class="flex flex-col gap-1">
		{#each block as item (item.item.axis + item.item.filler)}
			{@render constituent(item, label !== null)}
		{/each}
	</ul>
	</div>
{/snippet}

<ul class="flex flex-col gap-3">
	{#each normalizedBlocks() as block (block[0].item.normalized_group_id ?? block[0].item.axis + block[0].item.filler)}
		<li>
			{@render normalizedBlock(block)}
		</li>
	{/each}
</ul>
