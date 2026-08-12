import { describe, expect, it } from 'vitest';

import { ApiRequestError } from '$lib/api';
import { loadRemoteSearch } from './remote-search-load';

describe('loadRemoteSearch', () => {
	it('preserves the intentional instruction state when no query was submitted', async () => {
		await expect(loadRemoteSearch('', () => Promise.resolve({ total: 1 }))).resolves.toEqual({
			state: 'empty'
		});
	});

	it.each(['unavailable', 'timeout', 'rate-limited'] as const)(
		'returns the typed %s state separately from an empty result',
		async (state) => {
			const result = loadRemoteSearch(
				'melanoma',
				() => Promise.reject(new ApiRequestError(503, 'Service unavailable.', state))
			);

			await expect(result).resolves.toEqual({
				state: 'error',
				status: 503,
				remoteState: state,
				message: 'Service unavailable.'
			});
		}
	);

	it('does not launder an unexpected implementation failure', async () => {
		const defect = new Error('decoder defect');
		await expect(loadRemoteSearch('melanoma', () => Promise.reject(defect))).rejects.toBe(defect);
	});
});
