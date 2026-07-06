<script lang="ts" generics="P extends { total: number; hits: H[] }, H">
	import type { Snippet } from 'svelte';
	import { onMount } from 'svelte';
	import { createRepoBrowse } from '$lib/repo-browse.svelte';
	import RepoPageHeader from '$lib/components/RepoPageHeader.svelte';
	import RepoSearchBar from '$lib/components/RepoSearchBar.svelte';
	import RepoResultsCard from '$lib/components/RepoResultsCard.svelte';
	import Pagination from '$lib/components/Pagination.svelte';

	// Full browse/search page for a paginated repository (NCIt, caDSR): header, search
	// bar, results card, and pagination wired to the shared browse state. Each concrete
	// repo supplies its fetch functions, copy, and a `results` snippet for its own table.
	interface Props {
		title: string;
		description: string;
		helpText: Snippet;
		searchFn: (q: string, opts: { limit: number; offset: number }) => Promise<P>;
		listFn: (opts: { limit: number; offset: number }) => Promise<P>;
		placeholder: string;
		ariaLabel: string;
		suggestions: string[];
		suggestionsLabel?: string;
		browseTitle: string;
		countLabel: (total: number, mode: 'browse' | 'search') => string;
		results: Snippet<[H[]]>;
	}

	let {
		title,
		description,
		helpText,
		searchFn,
		listFn,
		placeholder,
		ariaLabel,
		suggestions,
		suggestionsLabel = 'Try:',
		browseTitle,
		countLabel,
		results
	}: Props = $props();

	const browse = createRepoBrowse<P>(
		(q, opts) => searchFn(q, opts),
		(opts) => listFn(opts)
	);
	onMount(() => browse.load(0, ''));

	const resultTitle = $derived(
		browse.mode === 'search' ? `Results for “${browse.submitted}”` : browseTitle
	);
	const label = $derived(browse.result ? countLabel(browse.result.total, browse.mode) : '');
</script>

<svelte:head>
	<title>{title} · ONTOPRISM</title>
</svelte:head>

<RepoPageHeader {title} {description} total={browse.result?.total ?? null}>
	{#snippet help()}
		{@render helpText()}
	{/snippet}
</RepoPageHeader>

<RepoSearchBar
	bind:value={browse.q}
	{placeholder}
	{ariaLabel}
	{suggestions}
	{suggestionsLabel}
	loading={browse.loading}
	onsearch={browse.search}
	onsuggestion={browse.suggest}
/>

{#if browse.result || browse.error}
	<RepoResultsCard title={resultTitle} countLabel={label} loading={browse.loading} error={browse.error}>
		{@render results(browse.result?.hits ?? [])}
		<Pagination
			offset={browse.offset}
			limit={browse.limit}
			total={browse.result?.total ?? 0}
			onPage={(o) => browse.load(o, browse.submitted)}
		/>
	</RepoResultsCard>
{/if}
