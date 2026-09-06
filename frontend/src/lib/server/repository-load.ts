import { error, redirect } from '@sveltejs/kit';
import { isPageSize, type PageSize } from '$lib/grid-state';

type FilterSpec = Readonly<Record<string, readonly string[]>>;
type FilterState<Filters extends FilterSpec> = { [Key in keyof Filters]: Array<Filters[Key][number] & string> };

export interface OffsetGridState<Sort extends string, Filters extends FilterSpec = FilterSpec> {
	size: PageSize;
	offset: number;
	sort: Sort;
	filters: FilterState<Filters>;
}

export interface OffsetGridSpec<Sort extends string, Filters extends FilterSpec = FilterSpec> {
	defaultSort: Sort;
	sorts: readonly Sort[];
	filters: Filters;
	resultWindow?: number;
}

export interface CursorGridSpec<Filters extends FilterSpec = FilterSpec> {
	filters: Filters;
}

export interface CursorGridState<Filters extends FilterSpec = FilterSpec> {
	query: string;
	size: PageSize;
	cursors: string[];
	filters: FilterState<Filters>;
}

interface PageResult { total: number; limit: number; offset: number; sort: string; }
interface Parsed<T> { value: T; invalid: boolean; }

function parseSize(params: URLSearchParams): Parsed<PageSize> {
	const raw = params.get('size');
	const parsed = raw === null ? 25 : Number(raw);
	const value: PageSize = isPageSize(parsed) ? parsed : 25;
	return { value, invalid: raw !== null && value !== parsed };
}

function parseOffset(params: URLSearchParams, size: PageSize, resultWindow?: number): Parsed<number> {
	const raw = params.get('offset');
	const parsed = raw === null || !/^\d+$/.test(raw) ? 0 : Number(raw);
	const aligned = Number.isSafeInteger(parsed) && parsed >= 0 && parsed % size === 0 ? parsed : 0;
	const finalWindowOffset = resultWindow === undefined ? aligned : Math.floor((resultWindow - 1) / size) * size;
	const value = Math.min(aligned, finalWindowOffset);
	return { value, invalid: raw !== null && value !== parsed };
}

function parseSort<Sort extends string>(params: URLSearchParams, spec: OffsetGridSpec<Sort>): Parsed<Sort> {
	const raw = params.get('sort');
	const value = raw !== null && spec.sorts.some((sort) => sort === raw) ? raw as Sort : spec.defaultSort;
	return { value, invalid: raw !== null && value !== raw };
}

function parseFilters<Filters extends FilterSpec>(params: URLSearchParams, spec: { filters: Filters }): Parsed<FilterState<Filters>> {
	const value: Record<string, string[]> = {};
	let invalid = false;
	for (const [key, allowed] of Object.entries(spec.filters)) {
		const selected = new Set(params.getAll(key));
		const invalidSelection = [...selected].some((item) => !allowed.includes(item));
		if (invalidSelection) invalid = true;
		value[key] = invalidSelection ? [] : allowed.filter((item) => selected.has(item));
	}
	return { value: value as FilterState<Filters>, invalid };
}

function canonicalCursorGridSearch<Filters extends FilterSpec>(state: CursorGridState<Filters>, spec: CursorGridSpec<Filters>): string {
	const params = new URLSearchParams();
	if (state.query) params.set('q', state.query);
	if (state.size !== 25) params.set('size', String(state.size));
	for (const cursor of state.cursors) params.append('cursor', cursor);
	for (const key of Object.keys(spec.filters).sort()) {
		for (const value of state.filters[key] ?? []) params.append(key, value);
	}
	return params.toString();
}

export function parseCursorGridUrl<Filters extends FilterSpec>(url: URL, spec: CursorGridSpec<Filters>): CursorGridState<Filters> {
	const rawQuery = url.searchParams.get('q') ?? '';
	const size = parseSize(url.searchParams);
	const filters = parseFilters(url.searchParams, spec);
	const rawCursors = url.searchParams.getAll('cursor');
	const invalidCursor = rawCursors.length > 50 || rawCursors.some((value) => !value || value.length > 1000);
	const cursors = invalidCursor ? [] : rawCursors;
	const state = { query: rawQuery.trim(), size: size.value, cursors, filters: filters.value };
	const canonical = canonicalCursorGridSearch(state, spec);
	if (rawQuery !== state.query || size.invalid || filters.invalid || invalidCursor || url.search.slice(1) !== canonical) {
		throw redirect(307, `${url.pathname}${canonical ? `?${canonical}` : ''}`);
	}
	return state;
}

export function canonicalOffsetGridSearch<Sort extends string, Filters extends FilterSpec>(query: string, state: OffsetGridState<Sort, Filters>, spec: OffsetGridSpec<Sort, Filters>): string {
	const params = new URLSearchParams();
	if (query) params.set('q', query);
	if (state.size !== 25) params.set('size', String(state.size));
	if (state.offset) params.set('offset', String(state.offset));
	if (state.sort !== spec.defaultSort) params.set('sort', state.sort);
	for (const key of Object.keys(spec.filters).sort()) for (const value of state.filters[key] ?? []) params.append(key, value);
	return params.toString();
}

export function parseOffsetGridUrl<Sort extends string, Filters extends FilterSpec>(url: URL, spec: OffsetGridSpec<Sort, Filters>): { query: string; state: OffsetGridState<Sort, Filters>; canonical: string } {
	const rawQuery = url.searchParams.get('q') ?? '';
	const query = rawQuery.trim();
	const size = parseSize(url.searchParams);
	const offset = parseOffset(url.searchParams, size.value, spec.resultWindow);
	const sort = parseSort(url.searchParams, spec);
	const filters = parseFilters(url.searchParams, spec);
	const invalid = rawQuery !== query || size.invalid || offset.invalid || sort.invalid || filters.invalid;
	const state = { size: size.value, offset: offset.value, sort: sort.value, filters: filters.value };
	const canonical = canonicalOffsetGridSearch(query, state, spec);
	if (invalid || url.search.slice(1) !== canonical) {
		throw redirect(307, `${url.pathname}${canonical ? `?${canonical}` : ''}`);
	}
	return { query, state, canonical };
}

export interface RepositoryPageData<T, Sort extends string, Filters extends FilterSpec = FilterSpec> { initial: { result: T; query: string } & OffsetGridState<Sort, Filters>; }

export async function loadRepositoryPage<T extends PageResult, Filters extends FilterSpec = FilterSpec>(
	url: URL,
	search: (query: string, state: OffsetGridState<T['sort'], Filters>) => Promise<T>,
	list: (state: OffsetGridState<T['sort'], Filters>) => Promise<T>,
	spec: OffsetGridSpec<T['sort'], Filters>
): Promise<RepositoryPageData<T, T['sort'], Filters>> {
	const { query, state } = parseOffsetGridUrl(url, spec);
	const result = await (query ? search(query, state) : list(state));
	if (result.limit !== state.size || result.offset !== state.offset || result.sort !== state.sort) error(502, 'Repository page metadata did not match the request.');
	const finalOffset = result.total === 0 ? 0 : Math.floor((result.total - 1) / state.size) * state.size;
	if (state.offset > finalOffset) {
		const corrected = { ...state, offset: finalOffset };
		const search = canonicalOffsetGridSearch(query, corrected, spec);
		throw redirect(307, `${url.pathname}${search ? `?${search}` : ''}`);
	}
	return { initial: { result, query, ...state } };
}
