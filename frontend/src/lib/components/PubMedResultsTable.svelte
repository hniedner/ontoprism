<script lang="ts">
	import { resolve } from '$app/paths';
	import type { PubMedArticleSummary } from '$lib/types';

	let { articles }: { articles: PubMedArticleSummary[] } = $props();

	const th = 'px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-muted';
</script>

<div class="overflow-x-auto">
	<table class="w-full border-collapse text-sm">
		<thead>
			<tr class="border-b border-default">
				<th class={th}>PMID</th>
				<th class={th}>Title</th>
				<th class={th}>Journal</th>
				<th class={th}>Date</th>
			</tr>
		</thead>
		<tbody>
			{#each articles as article (article.pmid)}
				<tr class="border-b border-default/60 transition-colors hover:bg-subtle">
					<td class="px-4 py-2.5 align-top">
						<a
							href={resolve('/repositories/pubmed/[pmid]', { pmid: article.pmid })}
							class="rounded bg-subtle px-1.5 py-0.5 font-mono text-xs text-primary-600 no-underline hover:text-primary-700 dark:text-primary-400"
						>
							{article.pmid}
						</a>
					</td>
					<td class="px-4 py-2.5 align-top">
						<a
							href={resolve('/repositories/pubmed/[pmid]', { pmid: article.pmid })}
							class="font-medium text-default no-underline hover:text-primary-600">{article.title}</a
						>
						{#if article.authors.length}
							<span class="ml-1 text-xs text-subtle">{article.authors.slice(0, 3).join(', ')}</span>
						{/if}
					</td>
					<td class="px-4 py-2.5 align-top text-muted">{article.journal ?? '—'}</td>
					<td class="px-4 py-2.5 align-top text-muted">{article.pub_date ?? '—'}</td>
				</tr>
			{/each}
		</tbody>
	</table>
</div>
