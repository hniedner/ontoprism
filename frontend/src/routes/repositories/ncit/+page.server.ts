import { listNcit, searchNcit } from '$lib/api';
import { critical } from '$lib/server/critical-load';
import { loadRepositoryPage } from '$lib/server/repository-load';
import type { RepresentationStatus, SearchPage } from '$lib/types';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, url }) => {
	const searching = Boolean(url.searchParams.get('q')?.trim());
	const spec = { defaultSort: searching ? 'relevance' : 'source', sorts: searching ? ['relevance', 'code:asc', 'code:desc', 'label:asc', 'label:desc'] : ['source', 'code:asc', 'code:desc', 'label:asc', 'label:desc'], filters: { representation_status: ['legacy-precoordinated'] } } as const;
	return loadRepositoryPage<SearchPage>(url,
		(query, state) => critical(searchNcit(query, { limit: state.size, offset: state.offset, sort: state.sort, representationStatus: state.filters.representation_status?.[0] as RepresentationStatus | undefined, fetch })),
		(state) => critical(listNcit({ limit: state.size, offset: state.offset, sort: state.sort, representationStatus: state.filters.representation_status?.[0] as RepresentationStatus | undefined, fetch })), spec);
};
