import { error } from '@sveltejs/kit';
import { ApiRequestError } from '$lib/api';
import { failed, ready, type LoadResult } from '$lib/load-result';

async function capture<T>(operation: Promise<T>): Promise<LoadResult<T>> {
	try {
		return ready(await operation);
	} catch (reason) {
		if (reason instanceof ApiRequestError) return failed(reason.status, reason.message);
		throw reason;
	}
}

export async function critical<T>(operation: Promise<T>): Promise<T> {
	const result = await capture(operation);
	if (result.state === 'ready') return result.data;
	if (result.state === 'error') error(result.status, result.message);
	throw new Error(`Critical load reached invalid ${result.state} state`);
}
