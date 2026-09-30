<script lang="ts" generics="P extends { total: number; hits: H[] }, H, Sort extends string">
	import type { Snippet } from 'svelte';
	import { SvelteURLSearchParams } from 'svelte/reactivity';
	import { goto } from '$app/navigation';
	import { navigating, page } from '$app/state';
	import RepoPageHeader from '$lib/components/RepoPageHeader.svelte';
	import RepoSearchBar from '$lib/components/RepoSearchBar.svelte';
	import RepoResultsCard from '$lib/components/RepoResultsCard.svelte';
	import Pagination from '$lib/components/Pagination.svelte';
	import type { DataTableFilterKeyMap, DataTableFilterState, DataTableIntent, DataTableOperations, DataTableSortState } from '$lib/components/data-table/types';
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
		initial: { result: P; query: string; offset: number; size: PageSize; sort: Sort; filters: Record<string, string[]>; textFilters?: Record<string, string> };
		defaultSort: Sort;
		sortKeys: Readonly<Record<string, Partial<Record<'asc' | 'desc', Sort>>>>;
		filterKeys: DataTableFilterKeyMap;
		textKeys?: DataTableFilterKeyMap;
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
		sortKeys,
		filterKeys,
		textKeys = {}
	}: Props = $props();

	let queryValue = $derived(initial.query);
	const mode = $derived(initial.query ? 'search' : 'browse');
	const loading = $derived(navigating.to?.url.pathname === page.url.pathname);
	const hasActiveFilters = $derived(Object.values(initial.filters).some((selected) => selected.length > 0) || Object.values(initial.textFilters ?? {}).some(Boolean));

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
	const tableFilters = $derived.by(() => {
		const mappedKeys = Object.values(filterKeys);
		const sourceKeys = Object.keys(initial.filters);
		if (new Set(mappedKeys).size !== mappedKeys.length || mappedKeys.some((key) => !Object.hasOwn(initial.filters, key)) || sourceKeys.some((key) => !mappedKeys.includes(key))) {
			throw new Error('Repository filter mappings do not match loaded filter state.');
		}
		if (Object.values(textKeys).some((key) => !key) || new Set(Object.values(textKeys)).size !== Object.values(textKeys).length || Object.keys(initial.textFilters ?? {}).some((key) => !Object.values(textKeys).includes(key))) {
			throw new Error('Repository text mappings do not match loaded filter state.');
		}
		if (Object.keys(filterKeys).some((key) => textKeys[key] === undefined) && Object.keys(textKeys).length) throw new Error('Categorical column is missing text filter mapping.');
		return Object.fromEntries([...new Set([...Object.keys(filterKeys), ...Object.keys(textKeys)])].map((columnId): [string, DataTableFilterState] => {
			const text = initial.textFilters?.[textKeys[columnId]] ?? '';
			const category = filterKeys[columnId];
			return [columnId, category ? { kind: 'categorical', selected: initial.filters[category], ...(text ? { text } : {}) } : { kind: 'text', text }];
		}));
	});
	const operations = $derived<DataTableOperations>({ kind: 'server', sort: sortState(initial.sort), defaultSort: sortState(defaultSort), activeSortLabel: sortLabel(initial.sort), filters: tableFilters, busy: loading, onintent: handleIntent });
	let intentError = $state<string | null>(null);
	function mappedFilterKey(columnId: string): string | null {
		const key = filterKeys[columnId];
		if (key !== undefined && Object.hasOwn(initial.filters, key)) return key;
		return null;
	}
	function mappedSort(intent: Extract<DataTableIntent, { kind: 'sort' }>): Sort | null {
		const value = sortKeys[intent.sort.key]?.[intent.sort.direction];
		if (value !== undefined) return value;
		intentError = `No server sort mapping for ${intent.sort.key}:${intent.sort.direction}`;
		return null;
	}
	function applySort(params: SvelteURLSearchParams, value: Sort): void {
		if (value !== defaultSort) params.set('sort', value); else params.delete('sort');
	}
	function applyFilter(params: SvelteURLSearchParams, key: string | null, intent: Extract<DataTableIntent, { kind: 'filter' }>): void {
		if (key !== null) params.delete(key);
		if (key !== null && intent.filter.kind === 'categorical') for (const value of intent.filter.selected) params.append(key, value);
		const textKey = textKeys[intent.columnId];
		if (textKey) { params.delete(`text_${textKey}`); if (intent.filter.text) params.set(`text_${textKey}`, intent.filter.text); }
	}
	function clearFilters(params: SvelteURLSearchParams): void {
		for (const key of Object.keys(initial.filters)) params.delete(key);
		for (const key of Object.values(textKeys)) params.delete(`text_${key}`);
	}
	function navigateTable(update: (params: SvelteURLSearchParams) => void): void {
		intentError = null;
		void navigate((params) => {
			params.delete('offset');
			update(params);
		});
	}
	function handleIntent(intent: DataTableIntent): void {
		if (intent.kind === 'sort') {
			const value = mappedSort(intent);
			if (value !== null) navigateTable((params) => applySort(params, value));
			return;
		}
		if (intent.kind === 'filter') {
			const key = mappedFilterKey(intent.columnId);
			if (key !== null || textKeys[intent.columnId]) navigateTable((params) => applyFilter(params, key, intent));
			else intentError = `No server filter mapping for ${intent.columnId}`;
			return;
		}
		if (intent.kind === 'clear-filter') {
			const key = mappedFilterKey(intent.columnId);
			if (key !== null || textKeys[intent.columnId]) navigateTable((params) => { if (key) params.delete(key); if (textKeys[intent.columnId]) params.delete(`text_${textKeys[intent.columnId]}`); });
			else intentError = `No server filter mapping for ${intent.columnId}`;
			return;
		}
		navigateTable((params) => {
			if (intent.kind === 'clear-filters') clearFilters(params);
			else { params.delete('sort'); params.delete('size'); clearFilters(params); }
		});
	}
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
		{#if intentError}<p role="alert" class="mb-2 text-sm text-danger">{intentError}</p>{/if}
		{@render results(initial.result.hits, operations, emptyMessage)}
		<Pagination
			offset={initial.offset}
			limit={initial.size}
			total={initial.result.total}
			onPage={(nextOffset) => load(nextOffset, initial.query)}
			onSize={(size) => navigate((params) => { params.delete('offset'); if (size === 25) params.delete('size'); else params.set('size', String(size)); })}
		/>
</RepoResultsCard>
