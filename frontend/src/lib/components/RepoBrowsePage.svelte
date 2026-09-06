<script lang="ts" generics="P extends { total: number; hits: H[] }, H, Sort extends string">
	import type { Snippet } from 'svelte';
	import { SvelteURLSearchParams } from 'svelte/reactivity';
	import { goto } from '$app/navigation';
	import { navigating, page } from '$app/state';
	import RepoPageHeader from '$lib/components/RepoPageHeader.svelte';
	import RepoSearchBar from '$lib/components/RepoSearchBar.svelte';
	import RepoResultsCard from '$lib/components/RepoResultsCard.svelte';
	import Pagination from '$lib/components/Pagination.svelte';
	import type { DataTableFilterState, DataTableIntent, DataTableOperations, DataTableSortState } from '$lib/components/data-table/types';
	import type { PageSize } from '$lib/grid-state';

	// Full browse/search page for a paginated local repository: header, search
	// bar, results card, and pagination over server-loaded URL state. Each concrete
	// repository supplies its copy and a `results` snippet for its own table.
	interface Props {
		title: string;
		description: string;
		route: string;
		helpText: Snippet;
		placeholder: string;
		ariaLabel: string;
		suggestions: string[];
		suggestionsLabel?: string;
		browseTitle: string;
		countLabel: (total: number, mode: 'browse' | 'search') => string;
		results: Snippet<[H[], DataTableOperations, string]>;
		filters?: Snippet;
		initial: { result: P; query: string; offset: number; size: PageSize; sort: Sort; filters: Record<string, string[]> };
		defaultSort: Sort;
		sortKeys: Readonly<Record<string, { asc: Sort; desc: Sort }>>;
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
		initial,
		defaultSort,
		sortKeys
	}: Props = $props();

	let queryValue = $derived(initial.query);
	const mode = $derived(initial.query ? 'search' : 'browse');
	const loading = $derived(navigating.to?.url.pathname === page.url.pathname);
	const hasActiveFilters = $derived(Object.values(initial.filters).some((selected) => selected.length > 0));

	async function navigate(update: (params: SvelteURLSearchParams) => void): Promise<void> {
		const params = new SvelteURLSearchParams(page.url.search);
		update(params);
		const search: '' | `?${string}` = params.size ? `?${params}` : '';
		const target = `${route}${search}`;
		// eslint-disable-next-line svelte/no-navigation-without-resolve -- callers pass a typed, resolved repository route; only URL state is appended here
		await goto(target);
	}
	async function load(nextOffset: number, term: string): Promise<void> { await navigate((params) => {
		const query = term.trim(); if (query) params.set('q', query); else params.delete('q');
		if (query !== initial.query) params.delete('sort');
		if (nextOffset) params.set('offset', String(nextOffset)); else params.delete('offset');
	}); }
	function sortState(value: Sort): DataTableSortState | null {
		for (const [key, sorts] of Object.entries(sortKeys)) {
			if (value === sorts.asc) return { key, direction: 'asc' };
			if (value === sorts.desc) return { key, direction: 'desc' };
		}
		return null;
	}
	function sortLabel(value: Sort): string {
		if (value === 'source') return 'Source order';
		if (value === 'relevance') return 'Relevance';
		const [key, direction] = value.split(':');
		return `${key.replaceAll('_', ' ')} ${direction === 'desc' ? 'descending' : 'ascending'}`;
	}
	const tableFilters = $derived(Object.fromEntries(Object.entries(initial.filters).map(([key, selected]) => [key, { kind: 'categorical', selected } satisfies DataTableFilterState])));
	const operations = $derived<DataTableOperations>({ kind: 'server', sort: sortState(initial.sort), defaultSort: sortState(defaultSort), activeSortLabel: sortLabel(initial.sort), filters: tableFilters, busy: loading, onintent: handleIntent });
	function applySort(params: SvelteURLSearchParams, intent: Extract<DataTableIntent, { kind: 'sort' }>): void {
		const value = sortKeys[intent.sort.key]?.[intent.sort.direction];
		if (value === undefined) throw new Error(`No server sort mapping for ${intent.sort.key}:${intent.sort.direction}`);
		if (value !== defaultSort) params.set('sort', value); else params.delete('sort');
	}
	function applyFilter(params: SvelteURLSearchParams, intent: Extract<DataTableIntent, { kind: 'filter' }>): void {
		params.delete(intent.columnId);
		for (const value of intent.filter.selected) params.append(intent.columnId, value);
	}
	function clearFilters(params: SvelteURLSearchParams): void {
		for (const key of Object.keys(initial.filters)) params.delete(key);
	}
	function handleIntent(intent: DataTableIntent): void { void navigate((params) => {
		params.delete('offset');
		if (intent.kind === 'sort') applySort(params, intent);
		else if (intent.kind === 'filter') applyFilter(params, intent);
		else if (intent.kind === 'clear-filter') params.delete(intent.columnId);
		else if (intent.kind === 'clear-filters') clearFilters(params);
		else { params.delete('sort'); params.delete('size'); clearFilters(params); }
	}); }
	const resultTitle = $derived(
		mode === 'search' ? `Results for “${initial.query}”` : browseTitle
	);
	const label = $derived(countLabel(initial.result.total, mode));
	const emptyMessage = $derived(
		mode === 'browse' && !hasActiveFilters
			? 'This repository contains no records.'
			: 'No records matched the current query and filters.'
	);
</script>

<svelte:head>
	<title>{title} · ONTOPRISM</title>
</svelte:head>

<RepoPageHeader {title} {description} total={initial.result.total} kind="local-certified-proxy">
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
		{@render results(initial.result.hits, operations, emptyMessage)}
		<Pagination
			offset={initial.offset}
			limit={initial.size}
			total={initial.result.total}
			onPage={(nextOffset) => load(nextOffset, initial.query)}
			onSize={(size) => navigate((params) => { params.delete('offset'); if (size === 25) params.delete('size'); else params.set('size', String(size)); })}
		/>
</RepoResultsCard>
