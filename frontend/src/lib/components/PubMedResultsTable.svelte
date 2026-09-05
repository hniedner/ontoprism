<script lang="ts">
	import { resolve } from '$app/paths';
	import type { PubMedArticleSummary } from '$lib/types';
	import DataTable from '$lib/components/data-table/DataTable.svelte';
	import type { DataTableColumn } from '$lib/components/data-table/types';

	let { articles }: { articles: readonly PubMedArticleSummary[] } = $props();
	const operations = { kind: 'client-page', scopeLabel: 'Filters and sorting apply only to the articles loaded on this page.' } as const;
	const columns: readonly DataTableColumn<PubMedArticleSummary>[] = [
		{ id: 'pmid', label: 'PMID', cell: pmidCell, sortValue: (article) => article.pmid, filter: { value: (article) => article.pmid, ariaLabel: 'Filter loaded article PMIDs' }, sticky: { side: 'left', offset: 0 } },
		{ id: 'title', label: 'Title', cell: titleCell, sortValue: (article) => article.title, filter: { value: (article) => `${article.title} ${article.authors.join(' ')}`, ariaLabel: 'Filter loaded article titles and authors' } },
		{ id: 'journal', label: 'Journal', cell: journalCell, sortValue: (article) => article.journal, filter: { value: (article) => article.journal, ariaLabel: 'Filter loaded article journals' } },
		{ id: 'date', label: 'Date', cell: dateCell, sortValue: (article) => article.pub_date, filter: { value: (article) => article.pub_date, ariaLabel: 'Filter loaded article dates' } }
	];
</script>

{#snippet pmidCell(article: PubMedArticleSummary)}
	<a href={resolve('/repositories/pubmed/[pmid]', { pmid: article.pmid })} class="rounded bg-subtle px-1.5 py-0.5 font-mono text-xs text-primary-600 no-underline hover:text-primary-700 dark:text-primary-400">{article.pmid}</a>
{/snippet}
{#snippet titleCell(article: PubMedArticleSummary)}
	<a href={resolve('/repositories/pubmed/[pmid]', { pmid: article.pmid })} class="font-medium text-default no-underline hover:text-primary-600">{article.title}</a>
	{#if article.authors.length}<span class="ml-1 text-xs text-subtle">{article.authors.slice(0, 3).join(', ')}</span>{/if}
{/snippet}
{#snippet journalCell(article: PubMedArticleSummary)}<span class="text-muted">{article.journal ?? '—'}</span>{/snippet}
{#snippet dateCell(article: PubMedArticleSummary)}<span class="text-muted">{article.pub_date ?? '—'}</span>{/snippet}

<DataTable rows={articles} {columns} caption="PubMed results loaded on this page" regionLabel="PubMed loaded-page results" getRowId={(article) => article.pmid} {operations} stickyHeader={true} />
