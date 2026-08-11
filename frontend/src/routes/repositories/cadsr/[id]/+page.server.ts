import { getCde } from '$lib/api';
import { critical } from '$lib/server/critical-load';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, params }) => ({
	cde: await critical(getCde(params.id, undefined, fetch))
});
