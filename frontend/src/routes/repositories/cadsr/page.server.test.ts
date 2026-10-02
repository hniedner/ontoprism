import { beforeEach, describe, expect, it, vi } from 'vitest';

const { getCadsrFilterDomains, listCadsr, searchCadsr } = vi.hoisted(() => ({
	getCadsrFilterDomains: vi.fn(),
	listCadsr: vi.fn(),
	searchCadsr: vi.fn()
}));
vi.mock('$lib/api', () => ({ getCadsrFilterDomains, listCadsr, searchCadsr }));

import { load } from './+page.server';

const domains = {
	value_domain_type: ['Enumerated'],
	workflow_status: ['RELEASED'],
	registration_status: ['Standard'],
	context: ['NCIP'],
	datatype: ['CHARACTER']
};

function page(overrides: Record<string, unknown> = {}) {
	return {
		query: '',
		total: 0,
		limit: 25,
		offset: 0,
		sort: 'source',
		filters: { workflow_status: ['RELEASED'] },
		column_text: { name: 'tumor' },
		hits: [],
		...overrides
	};
}

describe('caDSR page server contracts', () => {
	beforeEach(() => {
		getCadsrFilterDomains.mockReset().mockResolvedValue(domains);
		listCadsr.mockReset();
		searchCadsr.mockReset();
	});

	it('accepts canonical filter domains and response echoes', async () => {
		listCadsr.mockResolvedValue(page());
		const result = await load({
			url: new URL(
				'https://example.test/repositories/cadsr?workflow_status=RELEASED&text_name=tumor'
			),
			fetch: vi.fn()
		} as never);

		expect(result).toMatchObject({ initial: { result: page() }, domains });
	});

	it.each([
		['missing categorical echo', { filters: {} }],
		['extra categorical echo', { filters: { workflow_status: ['RELEASED'], context: ['NCIP'] } }],
		['wrong text value', { column_text: { name: 'wrong' } }],
		['extra text key', { column_text: { name: 'tumor', datatype: 'CHARACTER' } }],
		['missing text key', { column_text: {} }]
	])('fails closed on %s', async (_label, drift) => {
		listCadsr.mockResolvedValue(page(drift));
		await expect(
			load({
				url: new URL(
					'https://example.test/repositories/cadsr?workflow_status=RELEASED&text_name=tumor'
				),
				fetch: vi.fn()
			} as never)
		).rejects.toMatchObject({ status: 502 });
	});

	it('fails closed when a declared source domain is missing', async () => {
		const partial = { ...domains } as Partial<typeof domains>;
		delete partial.datatype;
		getCadsrFilterDomains.mockResolvedValue(partial);
		await expect(
			load({
				url: new URL('https://example.test/repositories/cadsr'),
				fetch: vi.fn()
			} as never)
		).rejects.toMatchObject({ status: 502 });
		expect(listCadsr).not.toHaveBeenCalled();
	});
});
