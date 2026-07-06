<script lang="ts">
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import { getArticle, getRelatedArticles } from '$lib/api.pubmed';
	import type { PubMedArticleDetail } from '$lib/types';
	import PubMedArticleHeader from '$lib/components/pubmed/PubMedArticleHeader.svelte';
	import PubMedArticleBody from '$lib/components/pubmed/PubMedArticleBody.svelte';

	let article = $state<PubMedArticleDetail | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let relatedPmids = $state<string[]>([]);

	$effect(() => {
		const pmid = page.params.pmid;
		if (!pmid) return;
		loading = true;
		error = null;
		article = null;
		relatedPmids = [];
		getArticle(pmid)
			.then((a) => (article = a))
			.catch((err) => (error = err instanceof Error ? err.message : String(err)))
			.finally(() => (loading = false));
		// Related articles are supplementary — a failure here must not block the article.
		getRelatedArticles(pmid, 'similar')
			.then((r) => (relatedPmids = r.related_pmids.slice(0, 10)))
			.catch(() => (relatedPmids = []));
	});
</script>

<svelte:head>
	<title>{article?.title ?? page.params.pmid} · PubMed · ONTOPRISM</title>
</svelte:head>

<a
	href={resolve('/repositories/pubmed')}
	class="mb-4 inline-flex items-center gap-1.5 text-sm text-muted no-underline hover:text-primary-600"
>
	<span aria-hidden="true">←</span> Back to PubMed search
</a>

{#if loading}
	<p class="text-sm text-muted">Loading {page.params.pmid}…</p>
{:else if error}
	<div
		class="rounded-xl border border-danger-200 bg-danger-50 p-4 text-sm text-danger dark:border-danger-800 dark:bg-danger-900/20"
	>
		Failed to load article: {error}
	</div>
{:else if article}
	<div class="space-y-5">
		<PubMedArticleHeader {article} />
		<PubMedArticleBody {article} />

		{#if relatedPmids.length}
			<div class="rounded-xl border border-default bg-card p-5 shadow-sm">
				<h2 class="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
					Similar articles
				</h2>
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
			</div>
		{/if}
	</div>
{/if}
