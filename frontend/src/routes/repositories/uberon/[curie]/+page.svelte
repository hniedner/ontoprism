<script lang="ts">
	import { resolve } from '$app/paths';
	import BrowserGraph from '$lib/components/BrowserGraph.svelte';
	import UberonConceptSummary from './UberonConceptSummary.svelte';
	import type { PageProps } from './$types';
	import RepositoryKindBadge from '$lib/components/RepositoryKindBadge.svelte';

	let { data }: PageProps = $props();
	const detail = $derived(data.detail);
</script>

<svelte:head><title>{detail.label ?? detail.code} · Uberon/CL · ONTOPRISM</title></svelte:head>

<a href={resolve('/repositories/uberon')} class="mb-4 inline-flex text-sm text-muted">← Back to search</a>

<div class="mb-4"><RepositoryKindBadge kind="local-certified-proxy" /></div>

<UberonConceptSummary {detail} />

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
