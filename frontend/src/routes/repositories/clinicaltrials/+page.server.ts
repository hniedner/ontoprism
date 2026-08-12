import { searchClinicalTrials } from '$lib/api.clinicaltrials';
import { empty, ready } from '$lib/load-result';
import { critical } from '$lib/server/critical-load';
import type { CTStudySearchPage } from '$lib/types';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, url }) => {
	const query = url.searchParams.get('q')?.trim() ?? '';
	if (!query) return { query, result: empty<CTStudySearchPage>() };
	const result = await critical(searchClinicalTrials({ condition: query, limit: 25 }, fetch));
	return { query, result: ready(result) };
};
