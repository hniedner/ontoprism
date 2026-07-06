<script lang="ts">
	import type { PubMedArticleDetail } from '$lib/types';

	let { article }: { article: PubMedArticleDetail } = $props();

	function authorName(a: { last_name: string | null; fore_name: string | null }): string {
		return [a.fore_name, a.last_name].filter(Boolean).join(' ');
	}
</script>

<div class="rounded-xl border border-default bg-card p-5 shadow-sm">
	<div class="flex flex-wrap items-center gap-2">
		<span
			class="rounded bg-subtle px-1.5 py-0.5 font-mono text-xs text-primary-600 dark:text-primary-400"
			>PMID {article.pmid}</span
		>
		{#if article.journal}<span class="text-xs text-muted">{article.journal}</span>{/if}
		{#if article.pub_date}<span class="text-xs text-subtle">· {article.pub_date}</span>{/if}
	</div>
	<h1 class="mt-2 text-lg font-semibold text-default">{article.title}</h1>
	{#if article.authors.length}
		<p class="mt-1 text-sm text-muted">{article.authors.map(authorName).join(', ')}</p>
	{/if}
	<div class="mt-3 flex flex-wrap gap-3 text-sm">
		<!-- External absolute URL (pubmed.ncbi.nlm.nih.gov); resolve() is for internal routes only. -->
		<!-- eslint-disable svelte/no-navigation-without-resolve -->
		<a
			href={article.url}
			target="_blank"
			rel="noopener noreferrer"
			class="text-primary-600 no-underline hover:text-primary-700">View on PubMed ↗</a
		>
		<!-- eslint-enable svelte/no-navigation-without-resolve -->
		{#if article.doi}<span class="text-muted">DOI: {article.doi}</span>{/if}
		{#if article.pmc_id}<span class="text-muted">{article.pmc_id}</span>{/if}
	</div>
</div>
