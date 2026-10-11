import { getTrial } from '#lib/api.clinicaltrials.ts';
import { critical } from '#lib/server/critical-load.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, params }) => ({
	trial: await critical(getTrial(params.nct, fetch))
});
