import { error } from '@sveltejs/kit';
import { ApiRequestError } from '$lib/api';
import { failed, ready, type LoadResult } from '$lib/load-result';

/** A settled critical load is either ready or an HTTP error — never empty/loading. */
type SettledResult<T> = Extract<LoadResult<T>, { state: 'ready' | 'error' }>;

async function capture<T>(operation: Promise<T>): Promise<SettledResult<T>> {
	try {
		return ready(await operation) as SettledResult<T>;
	} catch (reason) {
		if (reason instanceof ApiRequestError)
			return failed(reason.status, reason.message) as SettledResult<T>;
		throw reason;
	}
}

export async function critical<T>(operation: Promise<T>): Promise<T> {
	const result = await capture(operation);
	if (result.state === 'ready') return result.data;
	error(result.status, result.message);
}
