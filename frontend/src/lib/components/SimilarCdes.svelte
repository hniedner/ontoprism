<script lang="ts">
	import { resolve } from '$app/paths';
	import { similarCdes } from '$lib/api';
	import type { SimilarCde } from '$lib/types';

	let { publicId }: { publicId: string } = $props();

	let items = $state<SimilarCde[]>([]);
	let loaded = $state(false);
	let unavailable = $state(false);

	$effect(() => {
		loaded = false;
		unavailable = false;
		similarCdes(publicId, 10)
			.then((r) => (items = r))
			.catch(() => (unavailable = true))
			.finally(() => (loaded = true));
	});
</script>

<section class="rounded-xl border border-default bg-card p-4 shadow-sm">
	<h3 class="mb-3 flex items-center gap-2 text-sm font-semibold text-default">
		Similar CDEs
		<span class="rounded-full bg-subtle px-2 py-0.5 text-xs font-normal text-muted"
			>{loaded ? items.length : '…'}</span
		>
	</h3>
	{#if unavailable}
		<p class="text-sm italic text-subtle">Embeddings unavailable.</p>
	{:else if loaded && items.length === 0}
		<p class="text-sm italic text-subtle">None.</p>
	{:else}
		<ul class="flex flex-col gap-2">
			{#each items as cde (cde.public_id + cde.version)}
				<li class="flex items-center justify-between gap-2 text-sm">
					<a
						href={resolve('/repositories/cadsr/[id]', { id: cde.public_id })}
						class="min-w-0 truncate text-secondary no-underline hover:text-primary-600">{cde.long_name}</a
					>
					<span
						class="shrink-0 rounded bg-success-50 px-1.5 py-0.5 font-mono text-xs text-success tabular-nums dark:bg-success-900/30"
						>{cde.score.toFixed(2)}</span
					>
				</li>
			{/each}
		</ul>
	{/if}
</section>
