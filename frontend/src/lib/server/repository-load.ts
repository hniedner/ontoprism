import { parseOffset } from './query';

export interface RepositoryPageData<T, Extra extends object = Record<string, never>> {
	initial: { result: T; query: string; offset: number } & Extra;
}

export async function loadRepositoryPage<T, Extra extends object = Record<string, never>>(
	url: URL,
	search: (query: string, offset: number, extra?: Extra) => Promise<T>,
	list: (offset: number, extra?: Extra) => Promise<T>,
	readUrlState?: (params: URLSearchParams) => Extra
): Promise<RepositoryPageData<T, Extra>> {
	const query = url.searchParams.get('q')?.trim() ?? '';
	const offset = parseOffset(url.searchParams.get('offset'));
	const extra = readUrlState ? readUrlState(url.searchParams) : ({} as Extra);
	const result = await (query
		? readUrlState
			? search(query, offset, extra)
			: search(query, offset)
		: readUrlState
			? list(offset, extra)
			: list(offset));
	return { initial: { result, query, offset, ...extra } };
}
