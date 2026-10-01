import { error } from '@sveltejs/kit';
import { getCadsrFilterDomains, listCadsr, searchCadsr } from '$lib/api';
import { gridCapabilities, gridControls } from '$lib/repository-registry';
import { critical } from '$lib/server/critical-load';
import { loadRepositoryPage, type OffsetGridSpec } from '$lib/server/repository-load';
import type { CadsrFilterDomains, CdeRepositorySort, CdeSearchPage } from '$lib/types';
import type { PageServerLoad } from './$types';

function requireFilterEcho(result: CdeSearchPage, filters: Record<string, string[]>, text: Record<string, string>): void {
	if (Object.keys(filters).some((key) => (result.filters[key] ?? []).join('\0') !== filters[key].join('\0'))) error(502, 'caDSR page filters did not match the request.');
	if (Object.keys(text).length !== Object.keys(result.column_text).length || Object.entries(text).some(([key, value]) => result.column_text[key] !== value)) error(502, 'caDSR page text filters did not match the request.');
}

export const load: PageServerLoad = async ({ fetch, url }) => {
	const searching = Boolean(url.searchParams.get('q')?.trim());
	const sorts = gridCapabilities('cadsr').sorts[searching ? 'search' : 'list'] as CdeRepositorySort[];
	const domains: CadsrFilterDomains = await critical(getCadsrFilterDomains(fetch));
	const { filters, textFilters } = gridControls('cadsr', domains);
	const spec = { defaultSort: sorts[0], sorts, filters, textFilters } satisfies OffsetGridSpec<CdeRepositorySort>;
	const loaded = await loadRepositoryPage<CdeSearchPage>(url,
		(query, state) => critical(searchCadsr(query, { limit: state.size, offset: state.offset, sort: state.sort, filters: state.filters, columnText: state.textFilters, fetch })),
		(state) => critical(listCadsr({ limit: state.size, offset: state.offset, sort: state.sort, filters: state.filters, columnText: state.textFilters, fetch })), spec);
	requireFilterEcho(loaded.initial.result, loaded.initial.filters, loaded.initial.textFilters ?? {});
	return { ...loaded, domains };
};
