<script lang="ts">
	import { resolve } from '$app/paths';
	import type { DecompositionConstituent } from '$lib/types';

	interface AxisGroup {
		axis: string;
		label: string;
		items: DecompositionConstituent[];
	}

	let { axes }: { axes: AxisGroup[] } = $props();
</script>

<ul class="flex flex-col gap-3">
	{#each axes as group (group.axis)}
		<li>
			<div class="mb-1 font-mono text-xs uppercase tracking-wide text-muted">{group.label}</div>
			<ul class="flex flex-col gap-1">
				{#each group.items as c (c.axis + c.filler)}
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
					</li>
				{/each}
			</ul>
		</li>
	{/each}
</ul>
