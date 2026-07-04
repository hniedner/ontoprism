<script lang="ts">
	import { onMount } from 'svelte';
	import { listNcit, searchNcit } from '$lib/api';
	import type { SearchPage } from '$lib/types';
	import RepoPageHeader from '$lib/components/RepoPageHeader.svelte';
	import SearchResultsTable from '$lib/components/SearchResultsTable.svelte';
	import Pagination from '$lib/components/Pagination.svelte';

	const LIMIT = 25;
	const SUGGESTIONS = ['melanoma', 'thyroid carcinoma', 'BRCA1 gene', 'tumor stage', 'lung neoplasm'];

	let q = $state('');
	let submitted = $state('');
	let mode = $state<'browse' | 'search'>('browse');
	let offset = $state(0);
	let result = $state<SearchPage | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);

	async function load(nextOffset: number, term: string) {
		loading = true;
		error = null;
		try {
			const trimmed = term.trim();
			if (trimmed) {
				result = await searchNcit(trimmed, { limit: LIMIT, offset: nextOffset });
				mode = 'search';
			} else {
				result = await listNcit({ limit: LIMIT, offset: nextOffset });
				mode = 'browse';
			}
			submitted = trimmed;
			offset = nextOffset;
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
			result = null;
		} finally {
			loading = false;
		}
	}

	function run(event: SubmitEvent) {
		event.preventDefault();
		load(0, q);
	}

	function useSuggestion(term: string) {
		q = term;
		load(0, term);
	}

	onMount(() => load(0, ''));
</script>

<svelte:head>
	<title>NCIt Concepts · ONTOPRISM</title>
</svelte:head>

<RepoPageHeader
	title="NCIt Concepts"
	description="Browse and search NCI Thesaurus concepts. Explore the biomedical ontology hierarchy, concept roles, and semantically similar terms."
	total={result?.total ?? null}
>
	{#snippet help()}
		Search by term or synonym (e.g. <em>melanoma</em>). Click any concept to see its definition,
		hierarchy, typed roles, neighborhood graph, mapped caDSR CDEs, and embedding-based similar
		concepts.
	{/snippet}
</RepoPageHeader>

<!-- Search card -->
<div class="mb-6 rounded-xl border border-default bg-card p-5 shadow-sm">
	<form onsubmit={run} class="flex gap-2">
		<div class="relative flex-1">
			<svg
				viewBox="0 0 24 24"
				class="pointer-events-none absolute left-3 top-1/2 h-5 w-5 -translate-y-1/2 text-subtle"
				fill="none"
				stroke="currentColor"
				stroke-width="1.8"
			>
				<circle cx="11" cy="11" r="7" />
				<path d="m20 20-3.5-3.5" stroke-linecap="round" />
			</svg>
			<input
				type="search"
				bind:value={q}
				placeholder="Search NCIt concepts… e.g. breast cancer subtypes"
				aria-label="Search NCIt"
				class="w-full rounded-lg border border-default bg-page-bg py-2.5 pl-10 pr-3 text-sm text-default placeholder:text-subtle focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-500/30 dark:bg-neutral-900"
			/>
		</div>
		<button
			type="submit"
			disabled={loading}
			class="rounded-lg bg-primary-600 px-5 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-primary-700 disabled:opacity-50"
		>
			{loading ? 'Searching…' : 'Search'}
		</button>
	</form>

	<div class="mt-3 flex flex-wrap items-center gap-2">
		<span class="text-xs font-medium text-muted">Try:</span>
		{#each SUGGESTIONS as s (s)}
			<button
				type="button"
				onclick={() => useSuggestion(s)}
				class="rounded-full bg-subtle px-3 py-1 text-xs text-secondary transition-colors hover:bg-primary-50 hover:text-primary-700 dark:hover:bg-primary-900/30 dark:hover:text-primary-300"
			>
				{s}
			</button>
		{/each}
	</div>
</div>

<!-- Results -->
{#if error}
	<div
		class="rounded-xl border border-danger-200 bg-danger-50 p-4 text-sm text-danger dark:border-danger-800 dark:bg-danger-900/20"
	>
		Search failed: {error}
	</div>
{:else if result}
	<div class="overflow-hidden rounded-xl border border-default bg-card shadow-sm">
		<div class="flex items-center justify-between border-b border-default px-4 py-3">
			<h2 class="text-sm font-semibold text-default">
				{#if mode === 'search'}
					Results for “{submitted}”
				{:else}
					Browsing all concepts
				{/if}
			</h2>
			<span class="text-xs text-muted">
				{result.total.toLocaleString()}
				{mode === 'search' ? 'matches' : 'concepts'}
			</span>
		</div>
		{#if loading}
			<p class="px-4 py-6 text-center text-sm text-muted">Loading…</p>
		{:else}
			<SearchResultsTable hits={result.hits} />
			<Pagination {offset} limit={LIMIT} total={result.total} onPage={(o) => load(o, submitted)} />
		{/if}
	</div>
{:else if loading}
	<p class="text-center text-sm text-muted">Loading…</p>
{/if}
