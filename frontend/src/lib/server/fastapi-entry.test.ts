import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('$env/dynamic/private', () => ({
	env: {
		ONTOPRISM_FASTAPI_ORIGIN: 'http://fastapi.test:8011',
		ONTOPRISM_FASTAPI_TIMEOUT_MS: '200'
	}
}));

import { forwardFastApi } from './fastapi';

describe('configured FastAPI entry point', () => {
	afterEach(() => vi.unstubAllGlobals());

	it('uses the private runtime configuration for the production transport', async () => {
		const fetchMock = vi.fn().mockResolvedValue(Response.json({ configured: true }));
		vi.stubGlobal('fetch', fetchMock);
		const response = await forwardFastApi(
			new Request('http://node.test/api/v1/configured'),
			'/api/v1/configured'
		);

		expect(fetchMock).toHaveBeenCalledWith(
			new URL('http://fastapi.test:8011/api/v1/configured'),
			expect.objectContaining({ method: 'GET', redirect: 'manual' })
		);
		expect(await response.json()).toEqual({ configured: true });
	});
});
