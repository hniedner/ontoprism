<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import { listCadsr, searchCadsr } from '$lib/api';
	import type { CdeSearchPage } from '$lib/types';
	import RepoPageHeader from '$lib/components/RepoPageHeader.svelte';
	import Pagination from '$lib/components/Pagination.svelte';

	const LIMIT = 25;
	const SUGGESTIONS = ['tumor stage', 'age at diagnosis', 'race', 'gender', 'treatment response'];

	let q = $state('');
	let submitted = $state('');
	let mode = $state<'browse' | 'search'>('browse');
	let offset = $state(0);
	let result = $state<CdeSearchPage | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);

	async function load(nextOffset: number, term: string) {
		loading = true;
		error = null;
		try {
			const trimmed = term.trim();
			if (trimmed) {
				result = await searchCadsr(trimmed, { limit: LIMIT, offset: nextOffset });
				mode = 'search';
			} else {
				result = await listCadsr({ limit: LIMIT, offset: nextOffset });
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

	const th = 'px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-muted';
</script>

<svelte:head>
	<title>caDSR CDEs · ONTOPRISM</title>
</svelte:head>

<RepoPageHeader
	title="caDSR CDEs"
	description="Browse and search caDSR Common Data Elements. Each CDE links to NCIt concepts (ISO-11179 roles), permissible values, and semantically similar elements."
	total={result?.total ?? null}
>
	{#snippet help()}
		Search CDEs by name, long name, or public ID. Open a CDE to see its NCIt concept mappings,
		permissible values, and embedding-based similar CDEs. Concept links cross-navigate to the NCIt
		browser.
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
				placeholder="Search by name, definition, or CDE ID…"
				aria-label="Search caDSR"
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
		<span class="text-xs font-medium text-muted">Quick:</span>
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
				{mode === 'search' ? `Results for “${submitted}”` : 'Browsing all CDEs'}
			</h2>
			<span class="text-xs text-muted">{result.total.toLocaleString()} CDEs</span>
		</div>
		{#if loading}
			<p class="px-4 py-6 text-center text-sm text-muted">Loading…</p>
		{:else}
			<div class="overflow-x-auto">
				<table class="w-full border-collapse text-sm">
					<thead>
						<tr class="border-b border-default">
							<th class={th}>Public ID</th>
							<th class={th}>Name</th>
							<th class={th}>Context</th>
							<th class={th}>Type</th>
						</tr>
					</thead>
					<tbody>
						{#each result.hits as cde (cde.public_id + cde.version)}
							<tr class="border-b border-default/60 transition-colors hover:bg-subtle">
								<td class="px-4 py-2.5 align-top">
									<a
										href={resolve('/repositories/cadsr/[id]', { id: cde.public_id })}
										class="rounded bg-subtle px-1.5 py-0.5 font-mono text-xs text-primary-600 no-underline hover:text-primary-700 dark:text-primary-400"
									>
										{cde.public_id}
									</a>
									<span class="ml-1 text-xs text-subtle">v{cde.version}</span>
								</td>
								<td class="px-4 py-2.5 align-top">
									<a
										href={resolve('/repositories/cadsr/[id]', { id: cde.public_id })}
										class="font-medium text-default no-underline hover:text-primary-600">{cde.long_name}</a
									>
									{#if cde.short_name}
										<span class="ml-1 font-mono text-xs text-subtle">{cde.short_name}</span>
									{/if}
								</td>
								<td class="px-4 py-2.5 align-top text-muted">{cde.context ?? '—'}</td>
								<td class="px-4 py-2.5 align-top">
									{#if cde.datatype}
										<span
											class="rounded-md bg-info-50 px-2 py-0.5 text-xs font-medium text-info dark:bg-info-900/30"
											>{cde.datatype}</span
										>
									{:else}
										<span class="text-muted">—</span>
									{/if}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<Pagination {offset} limit={LIMIT} total={result.total} onPage={(o) => load(o, submitted)} />
		{/if}
	</div>
{:else if loading}
	<p class="text-center text-sm text-muted">Loading…</p>
{:else}
	<div class="rounded-xl border border-dashed border-default bg-card/50 px-6 py-12 text-center">
		<p class="text-sm text-muted">
			Enter a search term above to explore <span class="font-medium text-default">caDSR CDEs</span>.
		</p>
	</div>
{/if}
