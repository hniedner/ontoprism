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

	function normalizedBlocks(items: DecompositionConstituent[]) {
		const blocks = new SvelteMap<string, DecompositionConstituent[]>();
		for (const item of items) {
			const key = item.normalized_group_id ?? `outside-policy:${item.filler}`;
			blocks.set(key, [...(blocks.get(key) ?? []), item]);
		}
		return [...blocks.values()];
	}
</script>

{#snippet constituent(c: DecompositionConstituent)}
	<li class="flex items-center gap-2 text-sm">
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
		{#if c.axis_ambiguity_group_id}
			<span class="text-xs text-subtle">Axis ambiguity: {c.axis_ambiguity_group_id}</span>
		{/if}
	</li>
{/snippet}

{#snippet normalizedBlock(block: DecompositionConstituent[])}
	{#if block[0].normalized_group_label}
		<div class="text-xs font-medium text-muted">{block[0].normalized_group_label}</div>
	{/if}
	<ul class="flex flex-col gap-1">
		{#each block as item (item.axis + item.filler)}
			{@render constituent(item)}
		{/each}
	</ul>
{/snippet}

<ul class="flex flex-col gap-3">
	{#each axes as group (group.axis)}
		<li>
			<div class="mb-1 font-mono text-xs uppercase tracking-wide text-muted">{group.label}</div>
			{#each normalizedBlocks(group.items) as block (block[0].normalized_group_id ?? block[0].filler)}
				{@render normalizedBlock(block)}
			{/each}
		</li>
	{/each}
</ul>
