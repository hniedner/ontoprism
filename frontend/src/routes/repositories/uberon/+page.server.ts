import { listUberon, searchUberon } from '$lib/api';
import { critical } from '$lib/server/critical-load';
import { loadRepositoryPage, type OffsetGridSpec } from '$lib/server/repository-load';
import { error } from '@sveltejs/kit';
import type { UberonBrowseSort, UberonRepositoryPage, UberonRepositorySort, UberonSource } from '$lib/types';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, url }) => {
	const searching = Boolean(url.searchParams.get('q')?.trim());
	const spec = { defaultSort: searching ? 'relevance' : 'source', sorts: searching ? ['relevance', 'code:asc', 'code:desc', 'label:asc', 'label:desc'] : ['source', 'code:asc', 'code:desc', 'label:asc', 'label:desc'], filters: { source: ['uberon', 'cl'] } } satisfies OffsetGridSpec<UberonRepositorySort>;
	const source = (values: string[] | undefined): UberonSource | undefined => values?.length === 1 ? values[0] as UberonSource : undefined;
	const browseSort = (sort: UberonRepositorySort): UberonBrowseSort => {
		if (sort === 'relevance') error(500, 'Uberon browse state contained a search-only sort.');
		return sort;
	};
	const loaded = await loadRepositoryPage<UberonRepositoryPage>(url,
		(query, state) => critical(searchUberon(query, { limit: state.size, offset: state.offset, sort: state.sort, source: source(state.filters.source), fetch })),
		(state) => critical(listUberon({ limit: state.size, offset: state.offset, sort: browseSort(state.sort), source: source(state.filters.source), fetch })), spec);
	const expected = source(loaded.initial.filters.source) ?? null;
	if (!Object.hasOwn(loaded.initial.result, 'source') || loaded.initial.result.source !== expected) error(502, 'Uberon page filters did not match the request.');
	return loaded;
};
