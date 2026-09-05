import { listUberon, searchUberon } from '$lib/api';
import { critical } from '$lib/server/critical-load';
import { loadRepositoryPage, type OffsetGridSpec } from '$lib/server/repository-load';
import type { RepositorySort, UberonSearchPage, UberonSource } from '$lib/types';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, url }) => {
	const searching = Boolean(url.searchParams.get('q')?.trim());
	const spec = { defaultSort: searching ? 'relevance' : 'source', sorts: searching ? ['relevance', 'code:asc', 'code:desc', 'label:asc', 'label:desc'] : ['source', 'code:asc', 'code:desc', 'label:asc', 'label:desc'], filters: { source: ['uberon', 'cl'] } } satisfies OffsetGridSpec<RepositorySort>;
	const source = (values: string[] | undefined): UberonSource | undefined => values?.length === 1 ? values[0] as UberonSource : undefined;
	return loadRepositoryPage<UberonSearchPage>(url,
		(query, state) => critical(searchUberon(query, { limit: state.size, offset: state.offset, sort: state.sort, source: source(state.filters.source), fetch })),
		(state) => critical(listUberon({ limit: state.size, offset: state.offset, sort: state.sort, source: source(state.filters.source), fetch })), spec);
};
