import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('$env/dynamic/private', () => ({
	env: {
		ONTOPRISM_FASTAPI_ORIGIN: 'http://fastapi.test:8011',
		ONTOPRISM_FASTAPI_TIMEOUT_MS: '200',
		ICDO_ENTITLEMENT_KEY: 'server-only-entitlement'
	}
}));

import { icdoCodeSegment } from '$lib/api';
import { GET as proxyGet } from '../../routes/api/[...path]/+server';
import { load as detailLoad } from '../../routes/repositories/icdo/[edition]/[axis]/[code]/+page.server';
import { load as repositoryLoad } from '../../routes/repositories/icdo/[edition]/[axis]/+page.server';

function pageFetch(): typeof fetch {
	return async (input, init) => {
		const url = new URL(
			typeof input === 'string' ? input : input instanceof URL ? input.href : input.url,
			'http://node.test'
		);
		const request = new Request(url, {
			...init,
			headers: {
				...Object.fromEntries(new Headers(init?.headers)),
				'x-icdo-entitlement': 'browser-controlled-value'
			}
		});
		return (await proxyGet({
			getClientAddress: () => '203.0.113.9',
			params: { path: url.pathname.slice('/api/'.length) },
			request,
			url
		} as never)) as Response;
	};
}

describe('ICD-O repository page BFF boundary', () => {
	afterEach(() => vi.unstubAllGlobals());

	it.each([
		['3.2', 'morphology'],
		['4.0', 'morphology'],
		['4.0', 'topography']
	] as const)('loads %s/%s through the BFF with only the server entitlement', async (edition, axis) => {
		let upstreamHeaders = new Headers();
		vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
			upstreamHeaders = new Headers(init?.headers);
			const url = new URL(String(input));
			expect(url.pathname).toBe(`/api/v1/icdo/${edition}/${axis}/list`);
			return Response.json({
				edition,
				axis,
				activation_identity: 'a'.repeat(64),
				serving_identity: 'b'.repeat(64),
				query: '',
				total: 1,
				limit: 25,
				offset: 0,
				hits: [{ code: axis === 'topography' ? 'C00' : '8503/0', level: axis === 'topography' ? 'category' : 'morphology' }]
			});
		});
		if (typeof repositoryLoad !== 'function') throw new Error('repository load is not callable');

		const loaded = await repositoryLoad({
			fetch: pageFetch(),
			params: { edition, axis },
			url: new URL(`http://node.test/repositories/icdo/${edition}/${axis}`)
		} as never);

		expect(loaded).toMatchObject({ edition, axis, result: { total: 1 } });
		expect(upstreamHeaders.get('x-icdo-entitlement')).toBe('server-only-entitlement');
		expect(JSON.stringify(loaded)).not.toContain('server-only-entitlement');
	});

	it('round-trips a slash-bearing morphology code through the detail page and BFF', async () => {
		const segment = icdoCodeSegment('8503/0');
		vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
			const url = new URL(String(input));
			expect(url.pathname).toBe(`/api/v1/icdo/3.2/morphology/concepts/${segment}`);
			expect(new Headers(init?.headers).get('x-icdo-entitlement')).toBe(
				'server-only-entitlement'
			);
			return Response.json({
				edition: '3.2',
				axis: 'morphology',
				activation_identity: 'a'.repeat(64),
				serving_identity: 'b'.repeat(64),
				record: { code: '8503/0', level: 'morphology' },
				ncit_alignments: []
			});
		});
		if (typeof detailLoad !== 'function') throw new Error('detail load is not callable');

		const loaded = await detailLoad({
			fetch: pageFetch(),
			params: { edition: '3.2', axis: 'morphology', code: segment }
		} as never);

		if (!loaded) throw new Error('detail load returned no data');
		expect(loaded.record.code).toBe('8503/0');
	});
});
