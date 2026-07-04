<script lang="ts">
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import { getConcept, getNeighborhood } from '$lib/api';
	import type { ConceptDetail, Neighborhood } from '$lib/types';
	import RelationshipList from '$lib/components/RelationshipList.svelte';
	import NeighborhoodGraph from '$lib/components/NeighborhoodGraph.svelte';
	import MappedCdes from '$lib/components/MappedCdes.svelte';
	import SimilarConcepts from '$lib/components/SimilarConcepts.svelte';

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

<p class="back"><a href={resolve('/repositories/ncit')}>← Search</a></p>

{#if loading}
	<p>Loading {page.params.code}…</p>
{:else if error}
	<p class="error">{error}</p>
{:else if detail}
	<header>
		<h1>{detail.label ?? detail.code}</h1>
		<p class="meta">
			<code>{detail.code}</code>
			{#each detail.semantic_types as st (st)}<span class="tag">{st}</span>{/each}
		</p>
		{#if detail.definition}<p class="def">{detail.definition}</p>{/if}
		{#if detail.synonyms.length}
			<p class="syn"><strong>Synonyms:</strong> {detail.synonyms.join(', ')}</p>
		{/if}
	</header>

	{#if graph}
		<NeighborhoodGraph {graph} />
	{/if}

	<div class="panels">
		<section>
			<h3>Parents <span class="count">({detail.parents.length})</span></h3>
			<ul class="refs">
				{#each detail.parents as p (p.code)}
					<li>
						<a href={resolve('/repositories/ncit/[code]', { code: p.code })}>{p.label ?? p.code}</a>
					</li>
				{:else}
					<li class="empty">None.</li>
				{/each}
			</ul>
		</section>
		<section>
			<h3>Children <span class="count">({detail.children.length})</span></h3>
			<ul class="refs">
				{#each detail.children as c (c.code)}
					<li>
						<a href={resolve('/repositories/ncit/[code]', { code: c.code })}>{c.label ?? c.code}</a>
					</li>
				{:else}
					<li class="empty">None.</li>
				{/each}
			</ul>
		</section>
		<RelationshipList title="Roles" items={detail.roles} />
		<RelationshipList title="Associations" items={detail.associations} />
		<RelationshipList title="Incoming roles" items={detail.incoming_roles} />
		<MappedCdes code={detail.code} />
		<SimilarConcepts code={detail.code} />
	</div>
{/if}

<style>
	.back {
		margin: 0.5rem 0;
	}
	.meta {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		flex-wrap: wrap;
	}
	code {
		background: #f1f5f9;
		padding: 0.1rem 0.4rem;
		border-radius: 4px;
	}
	.tag {
		background: #ede9fe;
		color: #6d28d9;
		font-size: 0.75rem;
		padding: 0.1rem 0.5rem;
		border-radius: 999px;
	}
	.def {
		max-width: 60ch;
		color: #333;
	}
	.syn {
		font-size: 0.85rem;
		color: #555;
	}
	.panels {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
		gap: 1.5rem;
		margin-top: 1.5rem;
	}
	.refs {
		list-style: none;
		padding: 0;
		margin: 0;
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
		font-size: 0.88rem;
	}
	.count {
		color: #888;
		font-weight: 400;
	}
	.empty {
		color: #888;
		font-style: italic;
	}
	.error {
		color: #b91c1c;
	}
</style>
