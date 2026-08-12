<script lang="ts">
	import { resolve } from '$app/paths';
	import BrowserGraph from '$lib/components/BrowserGraph.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
	const detail = $derived(data.detail);
</script>

<svelte:head><title>{detail.label ?? detail.code} · Uberon/CL · ONTOPRISM</title></svelte:head>

<a href={resolve('/repositories/uberon')} class="mb-4 inline-flex text-sm text-muted">← Back to search</a>

<section class="rounded-xl border border-default bg-card p-5 shadow-sm">
	<div class="mb-2 flex items-center gap-2">
		<code>{detail.code}</code>
		<span class="rounded-full bg-subtle px-2 py-0.5 text-xs">{detail.source === 'cl' ? 'Cell Ontology' : 'Uberon'}</span>
	</div>
	<h1 class="text-2xl font-semibold text-default">{detail.label ?? detail.code}</h1>
	{#if detail.definition}<p class="mt-3 text-secondary">{detail.definition}</p>{/if}
	{#if detail.synonyms.length}<p class="mt-3 text-sm text-muted">Synonyms: {detail.synonyms.join('; ')}</p>{/if}
	{#if detail.truncated}
		<p role="status" class="mt-3 text-sm text-warning">
			This concept has additional relationships beyond the displayed detail limit.
		</p>
	{/if}
</section>

<section class="mt-6">
	<h2 class="mb-2 text-sm font-semibold text-default">Concept graph</h2>
	<BrowserGraph code={detail.code} initial={data.graph} repository="uberon" />
</section>

<div class="mt-6 grid gap-6 md:grid-cols-3">
	{#each [
		{ title: 'Parents', values: detail.parents },
		{ title: 'Children', values: detail.children }
	] as group (group.title)}
		<section class="rounded-xl border border-default bg-card p-4">
			<h2 class="mb-3 font-semibold">{group.title}</h2>
			<ul>{#each group.values as item (item.code)}<li><a href={resolve('/repositories/uberon/[curie]', { curie: item.code })}>{item.label ?? item.code}</a></li>{/each}</ul>
		</section>
	{/each}
	<section class="rounded-xl border border-default bg-card p-4">
		<h2 class="mb-3 font-semibold">Restrictions</h2>
		<ul>{#each detail.relations as relation (relation.kind + relation.target.code)}<li>{relation.relation_label ?? relation.relation} → <a href={resolve('/repositories/uberon/[curie]', { curie: relation.target.code })}>{relation.target.label ?? relation.target.code}</a></li>{/each}</ul>
	</section>
</div>
