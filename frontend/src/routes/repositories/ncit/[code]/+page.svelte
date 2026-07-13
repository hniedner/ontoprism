<script lang="ts">
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import { getConcept, getNeighborhood } from '$lib/api';
	import type { ConceptDetail, Neighborhood } from '$lib/types';
	import RelationshipList from '$lib/components/RelationshipList.svelte';
	import NeighborhoodGraph from '$lib/components/NeighborhoodGraph.svelte';
	import GraphExplorer from '$lib/components/GraphExplorer.svelte';
	import MappedCdes from '$lib/components/MappedCdes.svelte';
	import SimilarConcepts from '$lib/components/SimilarConcepts.svelte';
	import DecompositionPanel from '$lib/components/DecompositionPanel.svelte';
	import ExternalMappingsPanel from '$lib/components/ExternalMappingsPanel.svelte';

	let graphMode = $state<'interactive' | 'radial'>('interactive');

	let detail = $state<ConceptDetail | null>(null);
	let graph = $state<Neighborhood | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);

	$effect(() => {
		const code = page.params.code;
		if (!code) return;
		loading = true;
		error = null;
		detail = null;
		graph = null;
		Promise.all([getConcept(code), getNeighborhood(code)])
			.then(([d, g]) => {
				detail = d;
				graph = g;
			})
			.catch((err) => {
				error = err instanceof Error ? err.message : String(err);
			})
			.finally(() => {
				loading = false;
			});
	});
</script>

<svelte:head>
	<title>{detail?.label ?? page.params.code} · NCIt · ONTOPRISM</title>
</svelte:head>

<a
	href={resolve('/repositories/ncit')}
	class="mb-4 inline-flex items-center gap-1.5 text-sm text-muted no-underline hover:text-primary-600"
>
	<span aria-hidden="true">←</span> Back to search
</a>

{#if loading}
	<p class="text-sm text-muted">Loading {page.params.code}…</p>
{:else if error}
	<div
		class="rounded-xl border border-danger-200 bg-danger-50 p-4 text-sm text-danger dark:border-danger-800 dark:bg-danger-900/20"
	>
		{error}
	</div>
{:else if detail}
	<!-- Concept header -->
	<header class="mb-6">
		<h1 class="text-2xl font-semibold text-default">{detail.label ?? detail.code}</h1>
		<div class="mt-2 flex flex-wrap items-center gap-2">
			<span class="rounded bg-subtle px-2 py-0.5 font-mono text-xs text-secondary">{detail.code}</span>
			{#each detail.semantic_types as st (st)}
				<span
					class="rounded-full bg-primary-50 px-2.5 py-0.5 text-xs font-medium text-primary-700 dark:bg-primary-900/30 dark:text-primary-300"
					>{st}</span
				>
			{/each}
		</div>
		{#if detail.definition}
			<p class="mt-3 max-w-3xl text-sm leading-relaxed text-secondary">{detail.definition}</p>
		{/if}
		{#if detail.synonyms.length}
			<p class="mt-2 max-w-3xl text-sm text-muted">
				<span class="font-medium text-secondary">Synonyms:</span>
				{detail.synonyms.join(', ')}
			</p>
		{/if}
	</header>

	<!-- Interactive graph explorer -->
	<section>
		<div class="mb-2 flex items-center justify-between">
			<h2 class="text-sm font-semibold text-default">Concept graph</h2>
			<div class="inline-flex overflow-hidden rounded-lg border border-default text-xs">
				<button
					type="button"
					class="px-2.5 py-1 {graphMode === 'interactive'
						? 'bg-primary-600 text-white'
						: 'text-secondary hover:bg-subtle'}"
					onclick={() => (graphMode = 'interactive')}>Interactive</button
				>
				<button
					type="button"
					class="px-2.5 py-1 {graphMode === 'radial'
						? 'bg-primary-600 text-white'
						: 'text-secondary hover:bg-subtle'}"
					onclick={() => (graphMode = 'radial')}>Radial</button
				>
			</div>
		</div>
		{#if graphMode === 'interactive'}
			{#key detail.code}
				<GraphExplorer code={detail.code} initial={graph} />
			{/key}
		{:else if graph}
			<NeighborhoodGraph {graph} />
		{/if}
	</section>

	<!-- Hierarchy -->
	<div class="mt-6 grid gap-6 md:grid-cols-2">
		<section class="rounded-xl border border-default bg-card p-4 shadow-sm">
			<h3 class="mb-3 flex items-center gap-2 text-sm font-semibold text-default">
				Parents
				<span class="rounded-full bg-subtle px-2 py-0.5 text-xs font-normal text-muted"
					>{detail.parents.length}</span
				>
			</h3>
			<ul class="flex flex-col gap-2 text-sm">
				{#each detail.parents as p (p.code)}
					<li>
						<a
							href={resolve('/repositories/ncit/[code]', { code: p.code })}
							class="text-secondary no-underline hover:text-primary-600">{p.label ?? p.code}</a
						>
					</li>
				{:else}
					<li class="italic text-subtle">None.</li>
				{/each}
			</ul>
		</section>
		<section class="rounded-xl border border-default bg-card p-4 shadow-sm">
			<h3 class="mb-3 flex items-center gap-2 text-sm font-semibold text-default">
				Children
				<span class="rounded-full bg-subtle px-2 py-0.5 text-xs font-normal text-muted"
					>{detail.children.length}</span
				>
			</h3>
			<ul class="flex flex-col gap-2 text-sm">
				{#each detail.children as c (c.code)}
					<li>
						<a
							href={resolve('/repositories/ncit/[code]', { code: c.code })}
							class="text-secondary no-underline hover:text-primary-600">{c.label ?? c.code}</a
						>
					</li>
				{:else}
					<li class="italic text-subtle">None.</li>
				{/each}
			</ul>
		</section>
	</div>

	<!-- Relationship + semantic panels -->
	<div class="mt-6 grid gap-6 md:grid-cols-2 lg:grid-cols-3">
		<RelationshipList title="Roles" items={detail.roles} />
		<RelationshipList title="Associations" items={detail.associations} />
		<RelationshipList title="Incoming roles" items={detail.incoming_roles} />
		<DecompositionPanel code={detail.code} />
		<ExternalMappingsPanel code={detail.code} />
		<MappedCdes code={detail.code} />
		<SimilarConcepts code={detail.code} />
	</div>
{/if}
