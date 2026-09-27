<script lang="ts">
	import { resolve } from '$app/paths';
	import type { DecompositionConstituent, ConstituentEvidence } from '$lib/types';
    import ConstituentSource from './ConstituentSource.svelte';
    import ConstituentStatus from './ConstituentStatus.svelte';
	import { SvelteMap } from 'svelte/reactivity';

	interface AxisGroup {
		axis: string;
		label: string;
		items: DecompositionConstituent[];
	}

	let { axes, evidence = null, publicationStatus = null }: { axes: AxisGroup[]; evidence?: ConstituentEvidence[] | null; publicationStatus?: 'provisional' | null } = $props();

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
    {@const source = evidence?.find(row => row.axis === c.axis && row.filler_code === c.filler)}
	<li class="flex flex-wrap items-center gap-2 text-sm">
		{#if showAxis}
			<span class="font-mono text-xs uppercase tracking-wide text-muted">{display.axisLabel}</span>
		{/if}
		<a
			href={resolve('/repositories/ncit/[code]', { code: c.filler })}
			class="min-w-0 truncate text-secondary no-underline hover:text-primary-600"
			>{c.filler_label ?? c.filler}</a
		>
		<span class="font-mono text-xs text-subtle">{c.filler}</span>
        <ConstituentStatus constituent={c} {publicationStatus} />
        {#if source}<ConstituentSource evidence={source} />{/if}
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
