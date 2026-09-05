import { listCadsr, searchCadsr } from '$lib/api';
import { critical } from '$lib/server/critical-load';
import { loadRepositoryPage } from '$lib/server/repository-load';
import type { CdeSearchPage } from '$lib/types';
import type { PageServerLoad } from './$types';

const spec = { defaultSort: 'source', sorts: ['source', 'public_id:asc', 'public_id:desc', 'name:asc', 'name:desc'], filters: {} } as const;
export const load: PageServerLoad = async ({ fetch, url }) => loadRepositoryPage<CdeSearchPage>(url,
	(query, state) => critical(searchCadsr(query, { limit: state.size, offset: state.offset, sort: state.sort, fetch })),
	(state) => critical(listCadsr({ limit: state.size, offset: state.offset, sort: state.sort, fetch })), spec);
