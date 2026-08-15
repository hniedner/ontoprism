import { describe, expect, it, vi } from 'vitest';

import { readIcdoAccess } from './icdo-access';

describe('ICD-O access status', () => {
	it.each([
		[403, { detail: 'ICD-O entitlement required.' }, 'entitlement-required'],
		[503, { detail: 'unavailable' }, 'unavailable'],
		[200, { status: 'unexpected' }, 'unavailable'],
		[200, { status: 'ready-and-entitled' }, 'ready-and-entitled']
	] as const)('maps protected status %s to %s', async (status, body, expected) => {
		const fetcher = vi.fn().mockResolvedValue(Response.json(body, { status }));

		await expect(readIcdoAccess(fetcher)).resolves.toBe(expected);
		expect(fetcher).toHaveBeenCalledWith('/api/v1/icdo/access');
	});

	it('treats an unreachable status endpoint as unavailable', async () => {
		await expect(
			readIcdoAccess(vi.fn().mockRejectedValue(new TypeError('unreachable')))
		).resolves.toBe('unavailable');
	});
});
