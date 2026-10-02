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
		level: [],
		column_text: {},
		hits: [],
		...overrides
	};
}

describe('ICD-O page server filter echo contract', () => {
	beforeEach(() => {
		listIcdo.mockReset();
		searchIcdo.mockReset();
	});

	it('loads declared morphology text and behaviour controls without a level control', async () => {
		listIcdo.mockResolvedValue(page({ column_text: { code: '800', preferred: 'tumor' } }));
		const result = await load({
			url: new URL('https://example.test/repositories/icdo/4.0/morphology?behaviour=3&text_code=800&text_preferred=tumor'),
			params: { edition: '4.0', axis: 'morphology' },
			fetch: vi.fn()
		} as never);

		expect(result).toMatchObject({ initial: { result: { behaviour: ['3'], level: [], column_text: { code: '800', preferred: 'tumor' } } } });
		expect(listIcdo).toHaveBeenCalledWith(
			{ edition: '4.0', axis: 'morphology' },
			expect.objectContaining({ behaviour: ['3'], columnText: { code: '800', preferred: 'tumor' } })
		);
		expect(listIcdo.mock.calls[0][1]).not.toHaveProperty('level');
	});

	it.each([
		['behaviour', { behaviour: [] }],
		['missing behaviour', { behaviour: undefined }],
		['level', { level: ['morphology'] }],
		['text', { column_text: {} }]
	])('fails closed on %s response echo drift', async (_label, drift) => {
		listIcdo.mockResolvedValue(page({ column_text: { code: '800' }, ...drift }));
		await expect(load({
			url: new URL('https://example.test/repositories/icdo/4.0/morphology?behaviour=3&text_code=800'),
			params: { edition: '4.0', axis: 'morphology' },
			fetch: vi.fn()
		} as never)).rejects.toMatchObject({ status: 502 });
	});
});
