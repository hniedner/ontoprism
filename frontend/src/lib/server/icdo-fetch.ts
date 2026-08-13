import type { Cookies } from '@sveltejs/kit';

export function icdoFetch(fetch: typeof globalThis.fetch, cookies: Cookies): typeof globalThis.fetch {
	const entitlement = cookies.get('icdo_entitlement');
	return (input, init = {}) =>
		fetch(input, {
			...init,
			headers: {
				...Object.fromEntries(new Headers(init.headers)),
				...(entitlement ? { 'X-ICDO-Entitlement': entitlement } : {})
			}
		});
}
