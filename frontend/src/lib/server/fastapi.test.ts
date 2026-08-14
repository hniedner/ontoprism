import { describe, expect, it } from 'vitest';
import {
	forwardFastApiWith,
	type FastApiTransport
} from './fastapi-transport';
import { parseFastApiOrigin, parseFastApiTimeout } from './fastapi-config';

describe('FastAPI BFF transport', () => {
	it('validates a private HTTP origin and bounded timeout', () => {
		expect(parseFastApiOrigin('http://127.0.0.1:8011').href).toBe('http://127.0.0.1:8011/');
		expect(parseFastApiTimeout(undefined)).toBe(5_000);
		expect(parseFastApiTimeout('250')).toBe(250);
		for (const origin of [undefined, 'ftp://example.test', 'http://user@example.test', 'http://x/a']) {
			expect(() => parseFastApiOrigin(origin)).toThrow();
		}
		for (const timeout of ['0', '60001', '1.5', 'not-a-number']) {
			expect(() => parseFastApiTimeout(timeout)).toThrow();
		}
	});

	it('forwards method, query, body and end-to-end headers while preserving the upstream response', async () => {
		let observed: { url: string; init: RequestInit } | undefined;
		const transport: FastApiTransport = {
			origin: new URL('http://fastapi.test:8011'),
			timeoutMs: 200,
			fetch: async (input, init) => {
				observed = { url: String(input), init: init ?? {} };
				return new Response(JSON.stringify({ accepted: true }), {
					status: 202,
					headers: { 'content-type': 'application/json', 'x-contract': 'kept' }
				});
			}
		};
		const request = new Request('http://node.test/api/v1/example?release=26.07d', {
			method: 'POST',
			headers: {
				connection: 'x-remove-me',
				'content-encoding': 'gzip',
				'content-type': 'application/json',
				forwarded: 'for=198.51.100.1',
				host: 'node.test',
				'x-forwarded-for': '198.51.100.1',
				'x-remove-me': 'not end-to-end'
			},
			body: JSON.stringify({ code: 'C3262' })
		});

		const response = await forwardFastApiWith(
			request,
			'/api/v1/example?release=26.07d',
			transport,
			'203.0.113.9'
		);

		expect(observed?.url).toBe('http://fastapi.test:8011/api/v1/example?release=26.07d');
		expect(observed?.init.method).toBe('POST');
		const headers = new Headers(observed?.init.headers);
		expect(headers.has('host')).toBe(false);
		expect(headers.has('forwarded')).toBe(false);
		expect(headers.get('x-forwarded-for')).toBe('203.0.113.9');
		expect(headers.has('x-remove-me')).toBe(false);
		expect(headers.get('content-encoding')).toBe('gzip');
		expect(observed?.init.redirect).toBe('manual');
		expect(new TextDecoder().decode(observed?.init.body as ArrayBuffer)).toBe('{"code":"C3262"}');
		expect(response.status).toBe(202);
		expect(response.headers.get('x-contract')).toBe('kept');
		expect(await response.json()).toEqual({ accepted: true });
	});

	it.each([
		'/api/v1/icdo/4.0/morphology/list',
		'/api/v1/ncit/concepts/C1234/mappings',
		'/api/v1/ncit/concepts/C1234/decomposition',
		'/api/v1/mappings/$translate',
		'/api/v1/refresh'
	])('injects the configured entitlement for protected path %s', async (apiPath) => {
		let headers = new Headers();
		await forwardFastApiWith(
			new Request(`http://node.test${apiPath}`, {
				headers: {
					cookie: 'icdo_entitlement=browser-secret',
					'x-icdo-entitlement': 'forged-browser-secret'
				}
			}),
			apiPath,
			{
				origin: new URL('http://fastapi.test:8011'),
				timeoutMs: 200,
				icdoEntitlement: 'server-only-entitlement',
				fetch: async (_input, init) => {
					headers = new Headers(init?.headers);
					return Response.json({ ok: true });
				}
			},
			'203.0.113.9'
		);

		expect(headers.get('x-icdo-entitlement')).toBe('server-only-entitlement');
		expect(headers.has('cookie')).toBe(false);
	});

	it('strips browser entitlement when the server has no configured entitlement', async () => {
		let headers = new Headers();
		await forwardFastApiWith(
			new Request('http://node.test/api/v1/icdo/3.2/morphology/list', {
				headers: { 'x-icdo-entitlement': 'forged-browser-secret' }
			}),
			'/api/v1/icdo/3.2/morphology/list',
			{
				origin: new URL('http://fastapi.test:8011'),
				timeoutMs: 200,
				fetch: async (_input, init) => {
					headers = new Headers(init?.headers);
					return new Response('refused', { status: 403 });
				}
			},
			'203.0.113.9'
		);

		expect(headers.has('x-icdo-entitlement')).toBe(false);
	});

	it('does not send the configured entitlement to public FastAPI paths', async () => {
		let headers = new Headers();
		await forwardFastApiWith(
			new Request('http://node.test/api/v1/ncit/list'),
			'/api/v1/ncit/list',
			{
				origin: new URL('http://fastapi.test:8011'),
				timeoutMs: 200,
				icdoEntitlement: 'server-only-entitlement',
				fetch: async (_input, init) => {
					headers = new Headers(init?.headers);
					return Response.json({ ok: true });
				}
			},
			'203.0.113.9'
		);

		expect(headers.has('x-icdo-entitlement')).toBe(false);
	});

	it('does not expose the entitlement when an upstream request fails', async () => {
		const response = await forwardFastApiWith(
			new Request('http://node.test/api/v1/icdo/access'),
			'/api/v1/icdo/access',
			{
				origin: new URL('http://fastapi.test:8011'),
				timeoutMs: 200,
				icdoEntitlement: 'server-only-entitlement',
				fetch: async () => {
					throw new Error('server-only-entitlement');
				}
			},
			'203.0.113.9'
		);

		expect(response.status).toBe(503);
		expect(await response.text()).not.toContain('server-only-entitlement');
	});

	it('does not follow or expose cross-origin upstream redirects', async () => {
		const response = await forwardFastApiWith(
			new Request('http://node.test/api/v1/redirect'),
			'/api/v1/redirect',
			{
				origin: new URL('http://fastapi.test:8011'),
				timeoutMs: 200,
				fetch: async (_input, init) => {
					expect(init?.redirect).toBe('manual');
					return new Response(null, {
						status: 307,
						headers: { location: 'https://attacker.test/escaped' }
					});
				}
			},
			'203.0.113.9'
		);
		expect(response.status).toBe(307);
		expect(response.headers.has('location')).toBe(false);
	});

	it('times out while consuming a stalled upstream response body', async () => {
		const response = await forwardFastApiWith(
			new Request('http://node.test/api/v1/stream'),
			'/api/v1/stream',
			{
				origin: new URL('http://fastapi.test:8011'),
				timeoutMs: 20,
				fetch: async (_input, init) => {
					const signal = init?.signal;
					return new Response(
						new ReadableStream({
							start(controller) {
								controller.enqueue(new TextEncoder().encode('{'));
								signal?.addEventListener('abort', () => controller.error(signal.reason));
							}
						}),
						{ headers: { 'content-type': 'application/json' } }
					);
				}
			},
			'203.0.113.9'
		);
		expect(response.status).toBe(504);
		expect(await response.json()).toEqual({ detail: 'FastAPI request timed out' });
	});

	it('represents an unreachable origin as an explicit 503 response', async () => {
		const response = await forwardFastApiWith(
			new Request('http://node.test/api/v1/ncit/list'),
			'/api/v1/ncit/list',
			{
				origin: new URL('http://127.0.0.1:1'),
				timeoutMs: 50,
				fetch: async () => {
					throw new TypeError('connection refused');
				}
			},
			'203.0.113.9'
		);
		expect(response.status).toBe(503);
		expect(await response.json()).toEqual({ detail: 'FastAPI is unreachable' });
	});

	it('rewrites a same-origin /api redirect Location to a relative path', async () => {
		const response = await forwardFastApiWith(
			new Request('http://node.test/api/v1/redirect'),
			'/api/v1/redirect',
			{
				origin: new URL('http://fastapi.test:8011'),
				timeoutMs: 200,
				fetch: async () =>
					new Response(null, {
						status: 307,
						headers: { location: 'http://fastapi.test:8011/api/v1/target?x=1' }
					})
			},
			'203.0.113.9'
		);
		expect(response.status).toBe(307);
		expect(response.headers.get('location')).toBe('/api/v1/target?x=1');
	});

	it('refuses an upstream response whose declared size exceeds the cap', async () => {
		const response = await forwardFastApiWith(
			new Request('http://node.test/api/v1/big'),
			'/api/v1/big',
			{
				origin: new URL('http://fastapi.test:8011'),
				timeoutMs: 200,
				fetch: async () =>
					new Response('{}', {
						status: 200,
						headers: { 'content-length': String(33 * 1024 * 1024) }
					})
			},
			'203.0.113.9'
		);
		expect(response.status).toBe(502);
		expect(await response.json()).toEqual({ detail: 'FastAPI response is too large' });
	});

	it('refuses an upstream body that exceeds the cap without a declared size', async () => {
		const oversized = new Uint8Array(33 * 1024 * 1024);
		const response = await forwardFastApiWith(
			new Request('http://node.test/api/v1/big'),
			'/api/v1/big',
			{
				origin: new URL('http://fastapi.test:8011'),
				timeoutMs: 200,
				fetch: async () =>
					new Response(
						new ReadableStream({
							start(controller) {
								controller.enqueue(oversized);
								controller.close();
							}
						})
					)
			},
			'203.0.113.9'
		);
		expect(response.status).toBe(502);
		expect(await response.json()).toEqual({ detail: 'FastAPI response is too large' });
	});

	it('forwards a bodyless upstream status without attaching a body', async () => {
		const response = await forwardFastApiWith(
			new Request('http://node.test/api/v1/thing', { method: 'DELETE' }),
			'/api/v1/thing',
			{
				origin: new URL('http://fastapi.test:8011'),
				timeoutMs: 200,
				fetch: async () => new Response(null, { status: 204 })
			},
			'203.0.113.9'
		);
		// A 204/205/304 cannot carry a body; dropping the guard makes the Response
		// constructor throw and the request degrade to a spurious 503.
		expect(response.status).toBe(204);
		expect(response.body).toBeNull();
	});
});
