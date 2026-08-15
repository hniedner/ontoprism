import type { IcdoAccessStatus } from '$lib/types';

export async function readIcdoAccess(fetcher: typeof fetch): Promise<IcdoAccessStatus> {
	try {
		const response = await fetcher('/api/v1/icdo/access');
		if (response.status === 403) return 'entitlement-required';
		if (!response.ok) return 'unavailable';
		const body: unknown = await response.json();
		return typeof body === 'object' && body !== null && 'status' in body &&
			body.status === 'ready-and-entitled'
			? 'ready-and-entitled'
			: 'unavailable';
	} catch {
		return 'unavailable';
	}
}
