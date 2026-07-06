<script lang="ts">
	import type { PubMedArticleDetail } from '$lib/types';

	let { article }: { article: PubMedArticleDetail } = $props();

	const sectionTitle = 'mb-2 text-xs font-semibold uppercase tracking-wide text-muted';
	const card = 'rounded-xl border border-default bg-card p-5 shadow-sm';
</script>

{#if article.abstract}
	<div class={card}>
		<h2 class={sectionTitle}>Abstract</h2>
		<p class="whitespace-pre-line text-sm text-default">{article.abstract}</p>
	</div>
{/if}

{#if article.mesh_terms.length}
	<div class={card}>
		<h2 class={sectionTitle}>MeSH terms</h2>
		<div class="flex flex-wrap gap-1.5">
			{#each article.mesh_terms as term (term.descriptor)}
				<span
					class="rounded-full bg-subtle px-2.5 py-0.5 text-xs text-secondary"
					class:font-semibold={term.major_topic}>{term.descriptor}</span
				>
			{/each}
		</div>
	</div>
{/if}

{#if article.keywords.length}
	<div class={card}>
		<h2 class={sectionTitle}>Keywords</h2>
		<p class="text-sm text-muted">{article.keywords.join(', ')}</p>
	</div>
{/if}
