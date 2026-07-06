<script lang="ts">
	import type { NodeAttrs } from '$lib/graph/neighborhood-graph';

	interface Props {
		selected: NodeAttrs | null;
		expanding: boolean;
		onexpand: (code: string) => void;
		onopen: (code: string) => void;
	}

	let { selected, expanding, onexpand, onopen }: Props = $props();
</script>

{#if selected}
	<div class="mb-4">
		<h4 class="font-semibold text-default">{selected.label}</h4>
		<p class="mt-0.5 font-mono text-xs text-muted">{selected.code}</p>
		{#if selected.semanticType}
			<span
				class="mt-1 inline-block rounded-full bg-primary-50 px-2 py-0.5 text-xs text-primary-700 dark:bg-primary-900/30 dark:text-primary-300"
				>{selected.semanticType}</span
			>
		{/if}
		<dl class="mt-2 grid grid-cols-2 gap-1 text-xs text-muted">
			<dt>Degree</dt>
			<dd class="text-right text-default">{selected.degree ?? 0}</dd>
			<dt>Community</dt>
			<dd class="text-right text-default">#{(selected.community ?? 0) + 1}</dd>
		</dl>
		<div class="mt-3 flex flex-col gap-1.5">
			<button
				type="button"
				class="rounded-md bg-primary-600 px-2.5 py-1.5 text-xs font-medium text-white hover:bg-primary-700 disabled:opacity-50"
				disabled={expanding || selected.expanded}
				onclick={() => selected && onexpand(selected.code)}
			>
				{selected.expanded ? 'Expanded' : 'Expand node'}
			</button>
			<button
				type="button"
				class="rounded-md border border-default px-2.5 py-1.5 text-xs font-medium text-secondary hover:bg-subtle"
				onclick={() => selected && onopen(selected.code)}
			>
				Open concept →
			</button>
		</div>
	</div>
{:else}
	<p class="mb-4 text-xs text-subtle">
		Click a node to inspect it. Double-click to expand its neighborhood.
	</p>
{/if}
