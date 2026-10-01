import { error } from '@sveltejs/kit';
import { listIcdo, searchIcdo } from '$lib/api';
import { icdoDatasetKey, parseIcdoDataset, type IcdoPageFor } from '$lib/icdo-routes';
import { gridCapabilities, gridControls } from '$lib/repository-registry';
import { critical } from '$lib/server/critical-load';
import { loadRepositoryPage, type OffsetGridSpec } from '$lib/server/repository-load';
import type { IcdoBehaviour, IcdoRecordLevel, IcdoRepositorySort } from '$lib/types';
import type { PageServerLoad } from './$types';

function sameValues(left: readonly string[], right: readonly string[]): boolean {
	return left.length === right.length && left.every((value, index) => value === right[index]);
}

function requireFilterEcho(result: { behaviour?: unknown; level?: unknown; column_text?: unknown }, filters: { behaviour: readonly string[]; level: readonly string[]; columnText: Record<string, string> }): void {
	if (!Array.isArray(result.behaviour) || !Array.isArray(result.level) || !sameValues(result.behaviour, filters.behaviour) || !sameValues(result.level, filters.level)) error(502, 'ICD-O page filters did not match the request.');
	const echoed = result.column_text;
	if (!echoed || typeof echoed !== 'object' || Array.isArray(echoed) || Object.keys(echoed).length !== Object.keys(filters.columnText).length || Object.entries(echoed).some(([key, value]) => filters.columnText[key] !== value)) error(502, 'ICD-O page text filters did not match the request.');
}

export const load: PageServerLoad = async ({ fetch, params, url }) => {
	const dataset = parseIcdoDataset(params.edition, params.axis);
	if (!dataset) error(404, 'ICD-O dataset not found.');
	const datasetKey = icdoDatasetKey(dataset);
	const searching = Boolean(url.searchParams.get('q')?.trim());
	const sorts = gridCapabilities('icdo', datasetKey).sorts[searching ? 'search' : 'list'] as IcdoRepositorySort[];
	const { filters, textFilters } = gridControls('icdo', {}, datasetKey);
	if (dataset.axis === 'morphology') {
		const morphologyFilters: Record<'behaviour', readonly IcdoBehaviour[]> = {
			behaviour: filters.behaviour as readonly IcdoBehaviour[]
		};
		const spec = { defaultSort: sorts[0], sorts, filters: morphologyFilters, textFilters } satisfies OffsetGridSpec<IcdoRepositorySort, typeof morphologyFilters>;
		const loaded = await loadRepositoryPage<IcdoPageFor<typeof dataset>, typeof morphologyFilters>(url,
			(query, state) => critical(searchIcdo(dataset, query, { limit: state.size, offset: state.offset, sort: state.sort, behaviour: state.filters.behaviour, columnText: state.textFilters, fetch })),
			(state) => critical(listIcdo(dataset, { limit: state.size, offset: state.offset, sort: state.sort, behaviour: state.filters.behaviour, columnText: state.textFilters, fetch })), spec);
		requireFilterEcho(loaded.initial.result, { behaviour: loaded.initial.filters.behaviour, level: [], columnText: loaded.initial.textFilters ?? {} });
		return { ...dataset, ...loaded };
	}
	const topographyFilters: Record<'level', readonly IcdoRecordLevel[]> = {
		level: filters.level as readonly IcdoRecordLevel[]
	};
	const spec = { defaultSort: sorts[0], sorts, filters: topographyFilters, textFilters } satisfies OffsetGridSpec<IcdoRepositorySort, typeof topographyFilters>;
	const loaded = await loadRepositoryPage<IcdoPageFor<typeof dataset>, typeof topographyFilters>(url,
		(query, state) => critical(searchIcdo(dataset, query, { limit: state.size, offset: state.offset, sort: state.sort, level: state.filters.level, columnText: state.textFilters, fetch })),
		(state) => critical(listIcdo(dataset, { limit: state.size, offset: state.offset, sort: state.sort, level: state.filters.level, columnText: state.textFilters, fetch })), spec);
	requireFilterEcho(loaded.initial.result, { behaviour: [], level: loaded.initial.filters.level, columnText: loaded.initial.textFilters ?? {} });
	return { ...dataset, ...loaded };
};
