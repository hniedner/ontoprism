<script lang="ts">
	import { resolve } from '$app/paths';
	import { cdesForConcept } from '$lib/api';
	import type { CdeSummary } from '$lib/types';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import { handleLatest } from '$lib/latest';

	let { code }: { code: string } = $props();

	let cdes = $state<CdeSummary[]>([]);
	let loadState = $state<'loading' | 'ready' | 'error'>('loading');

	$effect(() => {
		loadState = 'loading';
		cdes = [];
		const controller = new AbortController();
		return handleLatest(
			cdesForConcept(code, 25, undefined, controller.signal),
			{
				ready: (result) => {
					cdes = result;
					loadState = 'ready';
				},
				failed: () => (loadState = 'error'),
				settled: () => undefined
			},
			() => controller.abort()
		);
	});
</script>

<section class="rounded-xl border border-default bg-card p-4 shadow-sm">
	<h3 class="mb-3 flex items-center gap-2 text-sm font-semibold text-default">
		Mapped caDSR CDEs
		<span class="rounded-full bg-subtle px-2 py-0.5 text-xs font-normal text-muted"
			>{loadState === 'ready' ? cdes.length : '…'}</span
		>
	</h3>
	{#if loadState === 'loading'}
		<LoadingState active label="Loading mapped CDEs" minHeight="4rem" />
	{:else if loadState === 'error'}
		<p role="alert" class="text-sm text-danger-600 dark:text-danger-400">
			Mapped CDEs are unavailable right now.
		</p>
	{:else if cdes.length === 0}
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
