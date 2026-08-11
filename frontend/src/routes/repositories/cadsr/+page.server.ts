import { listCadsr, searchCadsr } from '$lib/api';
import { critical } from '$lib/server/critical-load';
import { loadRepositoryPage } from '$lib/server/repository-load';
import type { PageServerLoad } from './$types';

const PAGE_SIZE = 25;

export const load: PageServerLoad = async ({ fetch, url }) => {
	return loadRepositoryPage(
		url,
		(query, offset) => critical(searchCadsr(query, { limit: PAGE_SIZE, offset, fetch })),
		(offset) => critical(listCadsr({ limit: PAGE_SIZE, offset, fetch }))
	);
};
