import { error } from '@sveltejs/kit';
import { listIcdo, searchIcdo } from '$lib/api';
import { parseIcdoDataset } from '$lib/icdo-routes';
import { critical } from '$lib/server/critical-load';
import { loadRepositoryPage, type OffsetGridSpec } from '$lib/server/repository-load';
import type { IcdoBehaviour, IcdoPage, IcdoRecordLevel, IcdoRepositorySort } from '$lib/types';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, params, url }) => {
	const dataset = parseIcdoDataset(params.edition, params.axis);
	if (!dataset) error(404, 'ICD-O dataset not found.');
	const levels: readonly IcdoRecordLevel[] = dataset.axis === 'morphology' ? ['morphology'] : ['category', 'leaf'];
	const behaviours: readonly IcdoBehaviour[] = dataset.axis === 'morphology' ? ['0','1','2','3','4','5','6','7','8','9'] : [];
	const spec = { defaultSort: 'source', sorts: ['source', 'code:asc', 'code:desc', 'preferred:asc', 'preferred:desc'], filters: { level: levels, behaviour: behaviours } } satisfies OffsetGridSpec<IcdoRepositorySort, { level: readonly IcdoRecordLevel[]; behaviour: readonly IcdoBehaviour[] }>;
	const loaded = await loadRepositoryPage<IcdoPage, typeof spec.filters>(url,
		(query, state) => critical(searchIcdo(dataset, query, { limit: state.size, offset: state.offset, sort: state.sort, level: state.filters.level, behaviour: state.filters.behaviour, fetch })),
		(state) => critical(listIcdo(dataset, { limit: state.size, offset: state.offset, sort: state.sort, level: state.filters.level, behaviour: state.filters.behaviour, fetch })), spec);
	return { ...dataset, ...loaded };
};
