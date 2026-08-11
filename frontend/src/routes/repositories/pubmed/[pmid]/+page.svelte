<script lang="ts">
	import { resolve } from '$app/paths';
	import PubMedArticleHeader from '$lib/components/pubmed/PubMedArticleHeader.svelte';
	import PubMedArticleBody from '$lib/components/pubmed/PubMedArticleBody.svelte';
	import RelatedArticles from '$lib/components/pubmed/RelatedArticles.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
	const article = $derived(data.article);
</script>

<svelte:head>
	<title>{article.title} · PubMed · ONTOPRISM</title>
</svelte:head>

<a
	href={resolve('/repositories/pubmed')}
	class="mb-4 inline-flex items-center gap-1.5 text-sm text-muted no-underline hover:text-primary-600"
>
	<span aria-hidden="true">←</span> Back to PubMed search
</a>

	<div class="space-y-5">
		<PubMedArticleHeader {article} />
		<PubMedArticleBody {article} />

		<RelatedArticles pmid={article.pmid} />
	</div>
