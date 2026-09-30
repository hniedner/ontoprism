import { error } from '@sveltejs/kit';
import { getNcitSemanticTypes, listNcit, searchNcit } from '$lib/api';
import { critical } from '$lib/server/critical-load';
import { loadRepositoryPage, type OffsetGridSpec } from '$lib/server/repository-load';
import type { NcitBrowseSort, NcitRepositoryPage, NcitRepositorySort, RepresentationStatus } from '$lib/types';
import type { PageServerLoad } from './$types';
import { gridCapabilities, gridControls } from '$lib/repository-registry';

export const load: PageServerLoad = async ({ fetch, url }) => {
	const searching = Boolean(url.searchParams.get('q')?.trim());
	const sorts = gridCapabilities('ncit').sorts[searching ? 'search' : 'list'] as NcitRepositorySort[];
	const domains = { semantic_type: await critical(getNcitSemanticTypes(fetch)) };
	const { filters, textFilters } = gridControls('ncit', domains);
	const spec = { defaultSort: sorts[0], sorts, filters, textFilters } satisfies OffsetGridSpec<NcitRepositorySort>;
	const browseSort = (sort: NcitRepositorySort): NcitBrowseSort => {
		if (sort === 'relevance') error(500, 'NCIt browse state contained a search-only sort.');
		return sort;
	};
	const loaded = await loadRepositoryPage<NcitRepositoryPage>(url,
		(query, state) => critical(searchNcit(query, { limit: state.size, offset: state.offset, sort: state.sort, representationStatus: state.filters.representation_status?.[0] as RepresentationStatus | undefined, columnText: state.textFilters, semanticTypes: state.filters.semantic_type, fetch })),
		(state) => critical(listNcit({ limit: state.size, offset: state.offset, sort: browseSort(state.sort), representationStatus: state.filters.representation_status?.[0] as RepresentationStatus | undefined, columnText: state.textFilters, semanticTypes: state.filters.semantic_type, fetch })), spec);
	const expected = loaded.initial.filters.representation_status[0] ?? null;
	if (!Object.hasOwn(loaded.initial.result, 'representation_status') || loaded.initial.result.representation_status !== expected) error(502, 'NCIt page filters did not match the request.');
	if (loaded.initial.result.semantic_types.join('\0') !== loaded.initial.filters.semantic_type.join('\0')) error(502, 'NCIt page filters did not match the request.');
	const echoed = loaded.initial.result.column_text;
	const requested = loaded.initial.textFilters ?? {};
	if (!echoed || Object.keys(echoed).length !== Object.keys(requested).length || Object.entries(echoed).some(([key, value]) => requested[key] !== value)) error(502, 'NCIt page text filters did not match the request.');
	return { ...loaded, domains };
};
