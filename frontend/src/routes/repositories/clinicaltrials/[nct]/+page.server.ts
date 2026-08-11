import { getTrial } from '$lib/api.clinicaltrials';
import { critical } from '$lib/server/critical-load';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, params }) => ({
	trial: await critical(getTrial(params.nct, fetch))
});
