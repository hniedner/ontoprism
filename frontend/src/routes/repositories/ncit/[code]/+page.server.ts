import { getConcept, getNeighborhood } from '$lib/api';
import { critical } from '$lib/server/critical-load';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, params }) => {
	const [detail, graph] = await critical(Promise.all([
		getConcept(params.code, fetch),
		getNeighborhood(params.code, 1, fetch)
	]));
	return { detail, graph };
};
