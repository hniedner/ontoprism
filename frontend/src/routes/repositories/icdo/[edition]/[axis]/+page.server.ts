import { error } from '@sveltejs/kit';
import { listIcdo, searchIcdo } from '$lib/api';
import { parseIcdoDataset } from '$lib/icdo-routes';
import { critical } from '$lib/server/critical-load';
import { loadRepositoryPage, type OffsetGridSpec } from '$lib/server/repository-load';
import type { IcdoPage, IcdoRepositorySort } from '$lib/types';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, params, url }) => {
	const dataset = parseIcdoDataset(params.edition, params.axis);
	if (!dataset) error(404, 'ICD-O dataset not found.');
	const levels = dataset.axis === 'morphology' ? ['morphology'] : ['category', 'leaf'];
	const spec = { defaultSort: 'source', sorts: ['source', 'code:asc', 'code:desc', 'preferred:asc', 'preferred:desc'], filters: { level: levels, behaviour: dataset.axis === 'morphology' ? ['0','1','2','3','4','5','6','7','8','9'] : [] } } satisfies OffsetGridSpec<IcdoRepositorySort>;
	const loaded = await loadRepositoryPage<IcdoPage>(url,
		(query, state) => critical(searchIcdo(dataset, query, { limit: state.size, offset: state.offset, sort: state.sort, level: state.filters.level, behaviour: state.filters.behaviour, fetch })),
		(state) => critical(listIcdo(dataset, { limit: state.size, offset: state.offset, sort: state.sort, level: state.filters.level, behaviour: state.filters.behaviour, fetch })), spec);
	return { ...dataset, ...loaded };
};
