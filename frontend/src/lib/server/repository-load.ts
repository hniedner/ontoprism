import { parseOffset } from './query';

export interface RepositoryPageData<T> {
	initial: { result: T; query: string; offset: number };
}

export async function loadRepositoryPage<T>(
	url: URL,
	search: (query: string, offset: number) => Promise<T>,
	list: (offset: number) => Promise<T>
): Promise<RepositoryPageData<T>> {
	const query = url.searchParams.get('q')?.trim() ?? '';
	const offset = parseOffset(url.searchParams.get('offset'));
	const result = await (query ? search(query, offset) : list(offset));
	return { initial: { result, query, offset } };
}
