<script lang="ts">
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import { getCde, getCdeNeighborhood } from '$lib/api';
	import type { CdeDetail, Neighborhood } from '$lib/types';
	import SimilarCdes from '$lib/components/SimilarCdes.svelte';
	import GraphExplorer from '$lib/components/GraphExplorer.svelte';

	let cde = $state<CdeDetail | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);

	// Concept graph, loaded on demand (a CDE view shouldn't fetch the subgraph eagerly).
	let cdeGraph = $state<Neighborhood | null>(null);
	let graphLoading = $state(false);
	let graphError = $state<string | null>(null);

	async function loadGraph() {
		const current = cde;
		if (!current) return;
		graphLoading = true;
		graphError = null;
		try {
			cdeGraph = await getCdeNeighborhood(current.public_id);
		} catch (err) {
			graphError = err instanceof Error ? err.message : String(err);
		} finally {
			graphLoading = false;
		}
	}

	$effect(() => {
		const id = page.params.id;
		if (!id) return;
		loading = true;
		error = null;
		cde = null;
		cdeGraph = null;
		graphError = null;
		getCde(id)
			.then((c) => (cde = c))
			.catch((err) => (error = err instanceof Error ? err.message : String(err)))
			.finally(() => (loading = false));
	});
</script>

<svelte:head>
	<title>{cde?.long_name ?? page.params.id} · caDSR · ONTOPRISM</title>
</svelte:head>

<a
	href={resolve('/repositories/cadsr')}
	class="mb-4 inline-flex items-center gap-1.5 text-sm text-muted no-underline hover:text-primary-600"
>
	<span aria-hidden="true">←</span> Back to CDE search
</a>

{#if loading}
	<p class="text-sm text-muted">Loading {page.params.id}…</p>
{:else if error}
	<div
		class="rounded-xl border border-danger-200 bg-danger-50 p-4 text-sm text-danger dark:border-danger-800 dark:bg-danger-900/20"
	>
		{error}
	</div>
{:else if cde}
	<header class="mb-6">
		<h1 class="text-2xl font-semibold text-default">{cde.long_name}</h1>
		<div class="mt-2 flex flex-wrap items-center gap-2">
			<span class="rounded bg-subtle px-2 py-0.5 font-mono text-xs text-secondary"
				>{cde.public_id} v{cde.version}</span
			>
			{#if cde.short_name}
				<span class="rounded bg-subtle px-2 py-0.5 font-mono text-xs text-muted">{cde.short_name}</span>
			{/if}
			{#if cde.context}
				<span
					class="rounded-full bg-primary-50 px-2.5 py-0.5 text-xs font-medium text-primary-700 dark:bg-primary-900/30 dark:text-primary-300"
					>{cde.context}</span
				>
			{/if}
			{#if cde.datatype}
				<span
					class="rounded-md bg-info-50 px-2 py-0.5 text-xs font-medium text-info dark:bg-info-900/30"
					>{cde.datatype}</span
				>
			{/if}
		</div>
		{#if cde.definition}
			<p class="mt-3 max-w-3xl text-sm leading-relaxed text-secondary">{cde.definition}</p>
		{/if}
	</header>

	<div class="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
		<!-- NCIt concepts -->
		<section class="rounded-xl border border-default bg-card p-4 shadow-sm">
			<h3 class="mb-3 flex items-center gap-2 text-sm font-semibold text-default">
				NCIt concepts
				<span class="rounded-full bg-subtle px-2 py-0.5 text-xs font-normal text-muted"
					>{cde.concepts.length}</span
				>
			</h3>
			<ul class="flex flex-col gap-2.5">
				{#each cde.concepts as c (c.concept_code)}
					<li class="flex flex-wrap items-baseline gap-1.5 text-sm">
						<a
							href={resolve('/repositories/ncit/[code]', { code: c.concept_code })}
							class="text-secondary no-underline hover:text-primary-600">{c.concept_name}</a
						>
						<span class="font-mono text-xs text-subtle">{c.concept_code}</span>
						{#if c.is_primary}
							<span
								class="rounded-full bg-success-50 px-1.5 py-0.5 text-xs font-medium text-success dark:bg-success-900/30"
								>primary</span
							>
						{/if}
						{#if c.concept_type}
							<span class="text-xs text-muted">{c.concept_type}</span>
						{/if}
					</li>
				{:else}
					<li class="text-sm italic text-subtle">None.</li>
				{/each}
			</ul>
		</section>

		<!-- Permissible values -->
		<section class="rounded-xl border border-default bg-card p-4 shadow-sm">
			<h3 class="mb-3 flex items-center gap-2 text-sm font-semibold text-default">
				Permissible values
				<span class="rounded-full bg-subtle px-2 py-0.5 text-xs font-normal text-muted"
					>{cde.permissible_values.length}</span
				>
			</h3>
			{#if cde.permissible_values.length}
				<ul class="flex flex-col gap-2 text-sm">
					{#each cde.permissible_values as pv (pv.value + (pv.meaning_code ?? ''))}
						<li class="flex flex-wrap items-baseline gap-1.5">
							<strong class="text-default">{pv.value}</strong>
							{#if pv.meaning}<span class="text-muted">— {pv.meaning}</span>{/if}
							{#if pv.meaning_code}<span class="font-mono text-xs text-subtle">{pv.meaning_code}</span
								>{/if}
						</li>
					{/each}
				</ul>
			{:else}
				<p class="text-sm italic text-subtle">Not an enumerated value domain.</p>
			{/if}
		</section>

		<SimilarCdes publicId={cde.public_id} />
	</div>

	<!-- Concept graph: the CDE joined into the NCIt graph via its mapped concepts -->
	<section class="mt-6">
		<div class="mb-2 flex items-center justify-between">
			<h2 class="text-sm font-semibold text-default">Concept graph</h2>
			{#if !cdeGraph}
				<button
					type="button"
					onclick={() => loadGraph()}
					disabled={graphLoading || cde.concepts.length === 0}
					class="rounded-lg border border-default px-2.5 py-1 text-xs text-secondary hover:bg-subtle disabled:opacity-50"
				>
					{graphLoading ? 'Loading…' : 'Explore in graph'}
				</button>
			{/if}
		</div>
		{#if graphError}
			<p class="text-sm text-danger">{graphError}</p>
		{:else if cdeGraph}
			<GraphExplorer code={cdeGraph.center} initial={cdeGraph} />
		{:else if cde.concepts.length === 0}
			<p class="text-sm italic text-subtle">No mapped NCIt concepts to graph.</p>
		{/if}
	</section>
{/if}
