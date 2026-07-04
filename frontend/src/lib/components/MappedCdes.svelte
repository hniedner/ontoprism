<script lang="ts">
	import { resolve } from '$app/paths';
	import { cdesForConcept } from '$lib/api';
	import type { CdeSummary } from '$lib/types';

	let { code }: { code: string } = $props();

	let cdes = $state<CdeSummary[]>([]);
	let loaded = $state(false);

	$effect(() => {
		loaded = false;
		cdesForConcept(code, 25)
			.then((c) => (cdes = c))
			.catch(() => (cdes = []))
			.finally(() => (loaded = true));
	});
</script>

<section class="rounded-xl border border-default bg-card p-4 shadow-sm">
	<h3 class="mb-3 flex items-center gap-2 text-sm font-semibold text-default">
		Mapped caDSR CDEs
		<span class="rounded-full bg-subtle px-2 py-0.5 text-xs font-normal text-muted"
			>{loaded ? cdes.length : '…'}</span
		>
	</h3>
	{#if loaded && cdes.length === 0}
		<p class="text-sm italic text-subtle">No CDEs map to this concept.</p>
	{:else}
		<ul class="flex flex-col gap-2">
			{#each cdes as cde (cde.public_id + cde.version)}
				<li class="flex flex-wrap items-baseline gap-1.5 text-sm">
					<a
						href={resolve('/repositories/cadsr/[id]', { id: cde.public_id })}
						class="text-secondary no-underline hover:text-primary-600">{cde.long_name}</a
					>
					<span class="font-mono text-xs text-subtle">{cde.public_id}</span>
				</li>
			{/each}
		</ul>
	{/if}
</section>
