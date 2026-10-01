<script lang="ts" generics="P extends { total: number; hits: H[] }, H, Sort extends string">
	import type { Snippet } from 'svelte';
	import { SvelteURLSearchParams } from 'svelte/reactivity';
	import { goto } from '$app/navigation';
	import { navigating, page } from '$app/state';
	import CursorPagination from '$lib/components/CursorPagination.svelte';
	import RepoPageHeader from '$lib/components/RepoPageHeader.svelte';
	import RepoSearchBar from '$lib/components/RepoSearchBar.svelte';
	import RepoResultsCard from '$lib/components/RepoResultsCard.svelte';
	import Pagination from '$lib/components/Pagination.svelte';
	import RemoteSearchSurface from '$lib/components/RemoteSearchSurface.svelte';
	import RemoteServiceDisclosure from '$lib/components/RemoteServiceDisclosure.svelte';
	import type { DataTableFilterKeyMap, DataTableFilterState, DataTableIntent, DataTableOperations, DataTableSortState } from '$lib/components/data-table/types';
	import type { PageSize } from '$lib/grid-state';

	// Full browse/search page for a paginated local or remote repository. Each
	// concrete repository supplies its copy and a `results` snippet for its table.
	interface SharedProps {
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
		noMatchesLabel?: (query: string) => string;
		navigationTotal?: number;
	}

	interface LocalProps {
		kind?: 'local-certified-proxy';
		instruction?: Snippet;
		remote?: never;
		cursor?: never;
	}

	interface RemoteProps {
		kind: 'remote-live-service';
		instruction: Snippet;
		remote: {
			service: 'NCBI PubMed' | 'ClinicalTrials.gov';
			state: 'empty' | 'ready' | 'error';
			error: { remoteState: 'unavailable' | 'rate-limited' | 'timeout'; message: string } | null;
		};
		cursor?: { trail: string[]; next: string | null };
	}

	type Props = SharedProps & (LocalProps | RemoteProps);

	let {
		title,
		description,
		route,
		kind,
		helpText,
		instruction,
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
		textKeys = {},
		noMatchesLabel,
		navigationTotal,
		remote,
		cursor
	}: Props = $props();

	let queryValue = $derived(initial.query);
	const mode = $derived(initial.query ? 'search' : 'browse');
	const repositoryKind = $derived(kind ?? 'local-certified-proxy');
	const remoteInstruction = $derived(instruction);
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
		params.delete('cursor');
	}); }
	function loadCursor(tokens: string[]): void {
		void navigate((params) => {
			params.delete('cursor');
			for (const token of tokens) params.append('cursor', token);
		});
	}
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
			params.delete('cursor');
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
			: noMatchesLabel?.(initial.query) ?? 'No records matched the current query and filters.'
	);
</script>

<svelte:head>
	<title>{title} · ONTOPRISM</title>
</svelte:head>

<RepoPageHeader {title} {description} total={remote && remote.state !== 'ready' ? null : initial.result.total} kind={repositoryKind}>
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

{#snippet resultCard()}
	<RepoResultsCard title={resultTitle} countLabel={label} {loading} error={null}>
		{#if intentError}<p role="alert" class="mb-2 text-sm text-danger">{intentError}</p>{/if}
		{@render results(initial.result.hits, operations, emptyMessage)}
		{#if cursor}
			<CursorPagination
				count={initial.result.hits.length}
				total={initial.result.total}
				hasPrevious={cursor.trail.length > 0}
				hasNext={Boolean(cursor.next)}
				size={initial.size}
				onPrevious={() => loadCursor(cursor.trail.slice(0, -1))}
				onNext={() => cursor.next && loadCursor([...cursor.trail, cursor.next])}
				onSize={(size) => navigate((params) => { params.delete('cursor'); if (size === 25) params.delete('size'); else params.set('size', String(size)); })}
			/>
		{:else}
			<Pagination
				offset={initial.offset}
				limit={initial.size}
				total={initial.result.total}
				navigationTotal={navigationTotal ?? initial.result.total}
				onPage={(nextOffset) => load(nextOffset, initial.query)}
				onSize={(size) => navigate((params) => { params.delete('offset'); if (size === 25) params.delete('size'); else params.set('size', String(size)); })}
			/>
		{/if}
	</RepoResultsCard>
{/snippet}

{#if remote && remoteInstruction}
	<RemoteServiceDisclosure service={remote.service} />
	<RemoteSearchSurface service={remote.service} ready={remote.state === 'ready'} error={remote.error} instruction={remoteInstruction}>
		{@render resultCard()}
	</RemoteSearchSurface>
{:else}
	{@render resultCard()}
{/if}
