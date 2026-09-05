<script lang="ts">
	import { resolve } from '$app/paths';
	import type { PubMedArticleSummary } from '$lib/types';
	import DataTable from '$lib/components/data-table/DataTable.svelte';
	import type { DataTableColumn, DataTableOperations } from '$lib/components/data-table/types';

	let { articles, operations = { kind: 'none' } }: { articles: readonly PubMedArticleSummary[]; operations?: DataTableOperations } = $props();
	const interactive = $derived(operations.kind === 'server');
	let columns = $derived.by((): readonly DataTableColumn<PubMedArticleSummary>[] => [
		{ id: 'pmid', label: 'PMID', cell: pmidCell, sticky: { side: 'left', offset: 0 } },
		{ id: 'title', label: 'Title', cell: titleCell },
		{ id: 'journal', label: 'Journal', cell: journalCell },
		{ id: 'date', label: 'Date', cell: dateCell, sortable: interactive ? ['desc'] : undefined }
	]);
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

<DataTable rows={articles} {columns} caption="PubMed repository results" regionLabel="PubMed repository results" getRowId={(article) => article.pmid} {operations} stickyHeader={true} />
