import { getJson } from '$lib/api';
import { critical } from '$lib/server/critical-load';
import type { IcdoCongruenceReport } from '$lib/types';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch }) => {
	const report = await critical(getJson<IcdoCongruenceReport>('/api/v1/icdo/4.0/topography/congruence',
		fetch));
	return { report };
};
