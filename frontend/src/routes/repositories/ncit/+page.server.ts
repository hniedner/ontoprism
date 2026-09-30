import { error } from '@sveltejs/kit';
import { listNcit, searchNcit } from '$lib/api';
import { critical } from '$lib/server/critical-load';
import { loadRepositoryPage, type OffsetGridSpec } from '$lib/server/repository-load';
import type { NcitBrowseSort, NcitRepositoryPage, NcitRepositorySort, RepresentationStatus } from '$lib/types';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, url }) => {
	const searching = Boolean(url.searchParams.get('q')?.trim());
	const spec = { defaultSort: searching ? 'relevance' : 'source', sorts: searching ? ['relevance', 'code:asc', 'code:desc', 'label:asc', 'label:desc'] : ['source', 'code:asc', 'code:desc', 'label:asc', 'label:desc'], filters: { representation_status: ['legacy-precoordinated'] }, textFilters: ['code', 'label', 'representation_status'] } satisfies OffsetGridSpec<NcitRepositorySort>;
	const browseSort = (sort: NcitRepositorySort): NcitBrowseSort => {
		if (sort === 'relevance') error(500, 'NCIt browse state contained a search-only sort.');
		return sort;
	};
	const loaded = await loadRepositoryPage<NcitRepositoryPage>(url,
		(query, state) => critical(searchNcit(query, { limit: state.size, offset: state.offset, sort: state.sort, representationStatus: state.filters.representation_status?.[0] as RepresentationStatus | undefined, columnText: state.textFilters, fetch })),
		(state) => critical(listNcit({ limit: state.size, offset: state.offset, sort: browseSort(state.sort), representationStatus: state.filters.representation_status?.[0] as RepresentationStatus | undefined, columnText: state.textFilters, fetch })), spec);
	const expected = loaded.initial.filters.representation_status[0] ?? null;
	if (!Object.hasOwn(loaded.initial.result, 'representation_status') || loaded.initial.result.representation_status !== expected) error(502, 'NCIt page filters did not match the request.');
	const echoed = loaded.initial.result.column_text;
	const requested = loaded.initial.textFilters ?? {};
	if (!echoed || Object.keys(echoed).length !== Object.keys(requested).length || Object.entries(echoed).some(([key, value]) => requested[key] !== value)) error(502, 'NCIt page text filters did not match the request.');
	return loaded;
};
