import { beforeEach, describe, expect, it, vi } from 'vitest';

const { listIcdo, searchIcdo } = vi.hoisted(() => ({ listIcdo: vi.fn(), searchIcdo: vi.fn() }));
vi.mock('$lib/api', () => ({ listIcdo, searchIcdo }));

import { load } from './+page.server';

function page(overrides: Record<string, unknown> = {}) {
	return {
		activation_identity: 'a'.repeat(64),
		serving_identity: 'b'.repeat(64),
		edition: '4.0',
		axis: 'morphology',
		query: '',
		total: 0,
		limit: 25,
		offset: 0,
		sort: 'source',
		behaviour: ['3'],
		level: ['morphology'],
		hits: [],
		...overrides
	};
}

describe('ICD-O page server filter echo contract', () => {
	beforeEach(() => {
		listIcdo.mockReset();
		searchIcdo.mockReset();
	});

	it('accepts canonical behaviour and level echoes from the list response', async () => {
		listIcdo.mockResolvedValue(page());
		const result = await load({
			url: new URL('https://example.test/repositories/icdo/4.0/morphology?behaviour=3&level=morphology'),
			params: { edition: '4.0', axis: 'morphology' },
			fetch: vi.fn()
		} as never);

		expect(result).toMatchObject({ initial: { result: { behaviour: ['3'], level: ['morphology'] } } });
	});

	it.each([
		['behaviour', { behaviour: [] }],
		['missing behaviour', { behaviour: undefined }],
		['level', { level: [] }]
	])('fails closed on %s response echo drift', async (_label, drift) => {
		listIcdo.mockResolvedValue(page(drift));
		await expect(load({
			url: new URL('https://example.test/repositories/icdo/4.0/morphology?behaviour=3&level=morphology'),
			params: { edition: '4.0', axis: 'morphology' },
			fetch: vi.fn()
		} as never)).rejects.toMatchObject({ status: 502 });
	});
});
