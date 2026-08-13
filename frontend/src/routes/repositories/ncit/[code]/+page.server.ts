import { getConcept, getMappings, getNeighborhood } from '$lib/api';
import { critical } from '$lib/server/critical-load';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, params }) => {
	const [detail, graph, mappings] = await critical(Promise.all([
		getConcept(params.code, fetch),
		getNeighborhood(params.code, 1, fetch),
		getMappings(params.code, fetch)
	]));
	return { detail, graph, mappings };
};
