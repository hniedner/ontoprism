import { error, redirect } from '@sveltejs/kit';

const PAGE_SIZES = [10, 25, 50, 100] as const;
export type PageSize = (typeof PAGE_SIZES)[number];

export interface OffsetGridState {
	size: PageSize;
	offset: number;
	sort: string;
	filters: Record<string, string[]>;
}

export interface OffsetGridSpec {
	defaultSort: string;
	sorts: readonly string[];
	filters: Readonly<Record<string, readonly string[]>>;
}

export interface CursorGridSpec {
	filters: Readonly<Record<string, readonly string[]>>;
}

export interface CursorGridState {
	query: string;
	size: PageSize;
	cursors: string[];
	filters: Record<string, string[]>;
}

interface PageResult { total: number; limit: number; offset: number; }
interface Parsed<T> { value: T; invalid: boolean; }

function parseSize(params: URLSearchParams): Parsed<PageSize> {
	const raw = params.get('size');
	const parsed = raw === null ? 25 : Number(raw);
	const value: PageSize = PAGE_SIZES.includes(parsed as PageSize) ? parsed as PageSize : 25;
	return { value, invalid: raw !== null && value !== parsed };
}

function parseOffset(params: URLSearchParams, size: PageSize): Parsed<number> {
	const raw = params.get('offset');
	const parsed = raw === null || !/^\d+$/.test(raw) ? 0 : Number(raw);
	const value = Number.isSafeInteger(parsed) && parsed >= 0 && parsed % size === 0 ? parsed : 0;
	return { value, invalid: raw !== null && value !== parsed };
}

function parseSort(params: URLSearchParams, spec: OffsetGridSpec): Parsed<string> {
	const raw = params.get('sort');
	const value = raw !== null && spec.sorts.includes(raw) ? raw : spec.defaultSort;
	return { value, invalid: raw !== null && value !== raw };
}

function parseFilters(params: URLSearchParams, spec: OffsetGridSpec): Parsed<Record<string, string[]>> {
	const value: Record<string, string[]> = {};
	let invalid = false;
	for (const [key, allowed] of Object.entries(spec.filters)) {
		const selected = new Set(params.getAll(key));
		if ([...selected].some((item) => !allowed.includes(item))) invalid = true;
		else if (selected.size) value[key] = allowed.filter((item) => selected.has(item));
	}
	return { value, invalid };
}

function canonicalCursorGridSearch(state: CursorGridState, spec: CursorGridSpec): string {
	const params = new URLSearchParams();
	if (state.query) params.set('q', state.query);
	if (state.size !== 25) params.set('size', String(state.size));
	for (const cursor of state.cursors) params.append('cursor', cursor);
	for (const key of Object.keys(spec.filters).sort()) {
		for (const value of state.filters[key] ?? []) params.append(key, value);
	}
	return params.toString();
}

export function parseCursorGridUrl(url: URL, spec: CursorGridSpec): CursorGridState {
	const rawQuery = url.searchParams.get('q') ?? '';
	const size = parseSize(url.searchParams);
	const filters = parseFilters(url.searchParams, { ...spec, defaultSort: '', sorts: [] });
	const cursors = url.searchParams.getAll('cursor');
	const invalidCursor = cursors.length > 50 || cursors.some((value) => !value || value.length > 1000);
	const state = { query: rawQuery.trim(), size: size.value, cursors, filters: filters.value };
	const canonical = canonicalCursorGridSearch(state, spec);
	if (rawQuery !== state.query || size.invalid || filters.invalid || invalidCursor || url.search.slice(1) !== canonical) {
		throw redirect(307, `${url.pathname}${canonical ? `?${canonical}` : ''}`);
	}
	return state;
}

export function canonicalOffsetGridSearch(query: string, state: OffsetGridState, spec: OffsetGridSpec): string {
	const params = new URLSearchParams();
	if (query) params.set('q', query);
	if (state.size !== 25) params.set('size', String(state.size));
	if (state.offset) params.set('offset', String(state.offset));
	if (state.sort !== spec.defaultSort) params.set('sort', state.sort);
	for (const key of Object.keys(spec.filters).sort()) for (const value of state.filters[key] ?? []) params.append(key, value);
	return params.toString();
}

export function parseOffsetGridUrl(url: URL, spec: OffsetGridSpec): { query: string; state: OffsetGridState; canonical: string } {
	const rawQuery = url.searchParams.get('q') ?? '';
	const query = rawQuery.trim();
	const size = parseSize(url.searchParams);
	const offset = parseOffset(url.searchParams, size.value);
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

export interface RepositoryPageData<T> { initial: { result: T; query: string } & OffsetGridState; }

export async function loadRepositoryPage<T extends PageResult>(
	url: URL,
	search: (query: string, state: OffsetGridState) => Promise<T>,
	list: (state: OffsetGridState) => Promise<T>,
	spec: OffsetGridSpec
): Promise<RepositoryPageData<T>> {
	const { query, state } = parseOffsetGridUrl(url, spec);
	const result = await (query ? search(query, state) : list(state));
	if (result.limit !== state.size || result.offset !== state.offset) error(502, 'Repository page metadata did not match the request.');
	const finalOffset = result.total === 0 ? 0 : Math.floor((result.total - 1) / state.size) * state.size;
	if (state.offset > finalOffset) {
		const corrected = { ...state, offset: finalOffset };
		const search = canonicalOffsetGridSearch(query, corrected, spec);
		throw redirect(307, `${url.pathname}${search ? `?${search}` : ''}`);
	}
	return { initial: { result, query, ...state } };
}
