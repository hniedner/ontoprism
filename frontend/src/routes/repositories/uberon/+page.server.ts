import { listUberon, searchUberon } from '$lib/api';
import { critical } from '$lib/server/critical-load';
import { loadRepositoryPage, type OffsetGridSpec } from '$lib/server/repository-load';
import { error } from '@sveltejs/kit';
import type { UberonBrowseSort, UberonRepositoryPage, UberonRepositorySort, UberonSource } from '$lib/types';
import type { PageServerLoad } from './$types';
import { gridCapabilities, gridControls } from '$lib/repository-registry';

export const load: PageServerLoad = async ({ fetch, url }) => {
	const searching = Boolean(url.searchParams.get('q')?.trim());
	const sorts = gridCapabilities('uberon').sorts[searching ? 'search' : 'list'] as UberonRepositorySort[];
	const { filters, textFilters } = gridControls('uberon');
	const spec = { defaultSort: sorts[0], sorts, filters, textFilters } satisfies OffsetGridSpec<UberonRepositorySort>;
	const sources = (values: string[] | undefined): UberonSource[] => (values ?? []) as UberonSource[];
	const browseSort = (sort: UberonRepositorySort): UberonBrowseSort => {
		if (sort === 'relevance') error(500, 'Uberon browse state contained a search-only sort.');
		return sort;
	};
	const loaded = await loadRepositoryPage<UberonRepositoryPage>(url,
		(query, state) => critical(searchUberon(query, { limit: state.size, offset: state.offset, sort: state.sort, sources: sources(state.filters.source), columnText: state.textFilters, fetch })),
		(state) => critical(listUberon({ limit: state.size, offset: state.offset, sort: browseSort(state.sort), sources: sources(state.filters.source), columnText: state.textFilters, fetch })), spec);
	const expected = sources(loaded.initial.filters.source);
	if (!Array.isArray(loaded.initial.result.sources) || loaded.initial.result.sources.join('\0') !== expected.join('\0')) error(502, 'Uberon page filters did not match the request.');
	const echoed = loaded.initial.result.column_text;
	const requested = loaded.initial.textFilters ?? {};
	if (!echoed || Object.keys(echoed).length !== Object.keys(requested).length || Object.entries(echoed).some(([key, value]) => requested[key] !== value)) error(502, 'Uberon page text filters did not match the request.');
	return loaded;
};
