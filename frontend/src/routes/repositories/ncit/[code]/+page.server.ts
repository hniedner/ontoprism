import { getAlignments, getConcept, getNeighborhood } from '$lib/api';
import { critical } from '$lib/server/critical-load';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ cookies, fetch, params }) => {
	const entitlement = cookies.get('icdo_entitlement');
	const entitledFetch: typeof fetch = (input, init = {}) => fetch(input, {
		...init,
		headers: {
			...Object.fromEntries(new Headers(init.headers)),
			...(entitlement ? { 'X-ICDO-Entitlement': entitlement } : {})
		}
	});
	const [detail, graph, mappings] = await critical(Promise.all([
		getConcept(params.code, fetch),
		getNeighborhood(params.code, 1, fetch),
		getAlignments(params.code, entitledFetch)
	]));
	return { detail, graph, mappings };
};
