import { getJson } from '$lib/api';
import { critical } from '$lib/server/critical-load';
import type { IcdoCongruenceReport } from '$lib/types';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ cookies, fetch }) => {
	const entitlement = cookies.get('icdo_entitlement');
	const report = await critical(getJson<IcdoCongruenceReport>('/api/v1/icdo/4.0/topography/congruence',
		(input, init = {}) => fetch(input, { ...init, headers: { ...Object.fromEntries(new Headers(init.headers)), ...(entitlement ? { 'X-ICDO-Entitlement': entitlement } : {}) } })));
	return { report };
};
