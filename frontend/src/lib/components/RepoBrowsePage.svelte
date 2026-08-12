<script lang="ts" generics="P extends { total: number; hits: H[] }, H">
	import type { Snippet } from 'svelte';
	import { SvelteURLSearchParams } from 'svelte/reactivity';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { navigating, page } from '$app/state';
	import RepoPageHeader from '$lib/components/RepoPageHeader.svelte';
	import RepoSearchBar from '$lib/components/RepoSearchBar.svelte';
	import RepoResultsCard from '$lib/components/RepoResultsCard.svelte';
	import Pagination from '$lib/components/Pagination.svelte';

	// Full browse/search page for a paginated repository (NCIt, caDSR): header, search
	// bar, results card, and pagination over server-loaded URL state. Each concrete
	// repository supplies its copy and a `results` snippet for its own table.
	interface Props {
		title: string;
		description: string;
		route: '/repositories/ncit' | '/repositories/cadsr' | '/repositories/uberon';
		helpText: Snippet;
		placeholder: string;
		ariaLabel: string;
		suggestions: string[];
		suggestionsLabel?: string;
		browseTitle: string;
		countLabel: (total: number, mode: 'browse' | 'search') => string;
		results: Snippet<[H[]]>;
		filters?: Snippet;
		initial: { result: P; query: string; offset: number };
	}

	let {
		title,
		description,
		route,
		helpText,
		placeholder,
		ariaLabel,
		suggestions,
		suggestionsLabel = 'Try:',
		browseTitle,
		countLabel,
		results,
		filters,
		initial
	}: Props = $props();

	let queryValue = $derived(initial.query);
	const mode = $derived(initial.query ? 'search' : 'browse');
	const loading = $derived(navigating.to?.url.pathname === page.url.pathname);

	async function load(nextOffset: number, term: string): Promise<void> {
		const params = new SvelteURLSearchParams(page.url.search);
		const query = term.trim();
		if (query) params.set('q', query);
		else params.delete('q');
		if (nextOffset) params.set('offset', String(nextOffset));
		else params.delete('offset');
		const search: '' | `?${string}` = params.size ? `?${params}` : '';
		const target = route === '/repositories/ncit'
			? resolve(`/repositories/ncit${search}`)
			: route === '/repositories/cadsr'
				? resolve(`/repositories/cadsr${search}`)
				: resolve(`/repositories/uberon${search}`);
		await goto(target);
	}

	const resultTitle = $derived(
		mode === 'search' ? `Results for “${initial.query}”` : browseTitle
	);
	const label = $derived(countLabel(initial.result.total, mode));
</script>

<svelte:head>
	<title>{title} · ONTOPRISM</title>
</svelte:head>

<RepoPageHeader {title} {description} total={initial.result.total}>
	{#snippet help()}
		{@render helpText()}
	{/snippet}
</RepoPageHeader>

<RepoSearchBar
	bind:value={queryValue}
	{placeholder}
	{ariaLabel}
	{suggestions}
	{suggestionsLabel}
	{loading}
	onsearch={() => load(0, queryValue)}
	onsuggestion={(term) => {
		queryValue = term;
		load(0, term);
	}}
/>

{#if filters}
	{@render filters()}
{/if}

<RepoResultsCard title={resultTitle} countLabel={label} {loading} error={null}>
	{@render results(initial.result.hits)}
	<Pagination
		offset={initial.offset}
		limit={25}
		total={initial.result.total}
		onPage={(nextOffset) => load(nextOffset, initial.query)}
	/>
</RepoResultsCard>
