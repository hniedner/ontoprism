import { searchClinicalTrials } from '$lib/api.clinicaltrials';
import { loadRemoteSearch } from '$lib/server/remote-search-load';
import type { CTStudySearchPage } from '$lib/types';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, url }) => {
	const query = url.searchParams.get('q')?.trim() ?? '';
	return {
		query,
		result: await loadRemoteSearch<CTStudySearchPage>(
			query,
			() => searchClinicalTrials({ condition: query, limit: 25 }, fetch)
		)
	};
};
