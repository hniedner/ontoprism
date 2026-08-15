import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('$env/dynamic/private', () => ({
	env: {
		ONTOPRISM_FASTAPI_ORIGIN: 'http://fastapi.test:8011',
		ONTOPRISM_FASTAPI_TIMEOUT_MS: '200',
		ICDO_ENTITLEMENT_KEY: 'server-only-entitlement'
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
			'/api/v1/configured',
			'203.0.113.9'
		);

		expect(fetchMock).toHaveBeenCalledWith(
			new URL('http://fastapi.test:8011/api/v1/configured'),
			expect.objectContaining({
				method: 'GET',
				redirect: 'manual',
				headers: expect.objectContaining({})
			})
		);
		expect(new Headers(fetchMock.mock.calls[0][1].headers).get('x-forwarded-for')).toBe(
			'203.0.113.9'
		);
		expect(
			new Headers(fetchMock.mock.calls[0][1].headers).get('x-icdo-entitlement')
		).toBeNull();
		expect(await response.json()).toEqual({ configured: true });
	});

	it('injects the private entitlement only for a protected upstream path', async () => {
		const fetchMock = vi.fn().mockResolvedValue(Response.json({ entitled: true }));
		vi.stubGlobal('fetch', fetchMock);
		await forwardFastApi(
			new Request('http://node.test/api/v1/icdo/3.2/morphology/list'),
			'/api/v1/icdo/3.2/morphology/list',
			'203.0.113.9'
		);

		expect(
			new Headers(fetchMock.mock.calls[0][1].headers).get('x-icdo-entitlement')
		).toBe('server-only-entitlement');
	});
});
