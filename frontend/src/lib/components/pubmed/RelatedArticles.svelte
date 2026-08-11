<script lang="ts">
	import { resolve } from '$app/paths';
	import { getRelatedArticles } from '$lib/api.pubmed';
	import LoadingState from '$lib/components/LoadingState.svelte';

	let { pmid }: { pmid: string } = $props();
	let relatedPmids = $state<string[]>([]);
	let loadState = $state<'loading' | 'ready' | 'error'>('loading');

	$effect(() => {
		let cancelled = false;
		loadState = 'loading';
		relatedPmids = [];
		async function load(): Promise<void> {
			try {
				const result = await getRelatedArticles(pmid, 'similar');
				if (cancelled) return;
				relatedPmids = result.related_pmids.slice(0, 10);
				loadState = 'ready';
			} catch {
				if (!cancelled) loadState = 'error';
			}
		}
		void load();
		return () => {
			cancelled = true;
		};
	});
</script>

<section class="rounded-xl border border-default bg-card p-5 shadow-sm" style:min-height="8rem">
	<h2 class="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Similar articles</h2>
	{#if loadState === 'loading'}
		<LoadingState active label="Loading similar articles" minHeight="4rem" />
	{:else if loadState === 'error'}
		<p role="alert" class="text-sm text-danger-600 dark:text-danger-400">
			Similar articles are unavailable right now.
		</p>
	{:else if relatedPmids.length === 0}
		<p class="text-sm italic text-subtle">No similar articles were returned.</p>
	{:else}
		<div class="flex flex-wrap gap-1.5">
			{#each relatedPmids as related (related)}
				<a
					href={resolve('/repositories/pubmed/[pmid]', { pmid: related })}
					class="rounded bg-subtle px-2 py-0.5 font-mono text-xs text-primary-600 no-underline hover:text-primary-700 dark:text-primary-400"
				>
					{related}
				</a>
			{/each}
		</div>
	{/if}
</section>
