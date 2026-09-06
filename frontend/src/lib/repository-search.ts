import { resolve } from '$app/paths';
import type { ResolvedPathname } from '$app/types';

export type SearchRepository = 'clinicaltrials' | 'pubmed';

export function repositorySearchHref(
	repository: SearchRepository,
	current: URL,
	term: string
): ResolvedPathname {
	const target = new URL(current);
	const query = term.trim();
	if (query) target.searchParams.set('q', query);
	else target.searchParams.delete('q');
	target.searchParams.delete('offset');
	target.searchParams.delete('cursor');
	target.searchParams.delete('sort');
	const queryString = target.search as '' | `?${string}`;
	return repository === 'clinicaltrials'
		? resolve(`/repositories/clinicaltrials${queryString}`)
		: resolve(`/repositories/pubmed${queryString}`);
}
