import { getJson } from '$lib/api';
import { critical } from '$lib/server/critical-load';
import type { IcdoRecord } from '$lib/types';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ cookies, fetch, params }) => {
	const entitlement = cookies.get('icdo_entitlement');
	const record = await critical(getJson<IcdoRecord>(`/api/v1/icdo/${params.edition}/${params.axis}/concepts/${params.code}`,
		(input, init = {}) => fetch(input, { ...init, headers: { ...Object.fromEntries(new Headers(init.headers)), ...(entitlement ? { 'X-ICDO-Entitlement': entitlement } : {}) } })));
	return { edition: params.edition, axis: params.axis, record };
};
