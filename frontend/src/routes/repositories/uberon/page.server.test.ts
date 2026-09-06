import { beforeEach, describe, expect, it, vi } from 'vitest';

const { listUberon, searchUberon } = vi.hoisted(() => ({
	listUberon: vi.fn(),
	searchUberon: vi.fn()
}));
vi.mock('$lib/api', () => ({ listUberon, searchUberon }));

import { load } from './+page.server';

describe('Uberon page server sort contract', () => {
	beforeEach(() => {
		listUberon.mockReset();
		searchUberon.mockReset();
	});

	it('requests default relevance order and rejects a mismatched response echo', async () => {
		searchUberon.mockResolvedValue({
			query: 'lung',
			total: 1,
			limit: 25,
			offset: 0,
			sort: 'source',
			hits: [{ code: 'UBERON:0002048', source: 'uberon', label: 'lung', matched_synonym: null }]
		});

		await expect(load({
			url: new URL('https://example.test/repositories/uberon?q=lung'),
			fetch: vi.fn()
		} as never)).rejects.toMatchObject({ status: 502 });
		expect(searchUberon).toHaveBeenCalledWith('lung', expect.objectContaining({ sort: 'relevance' }));
		expect(listUberon).not.toHaveBeenCalled();
	});

	it.each([
		['one selected source', '?source=cl', 'cl'],
		['unfiltered', '', null],
		['both selected sources', '?source=uberon&source=cl', null]
	])('accepts the canonical %s response echo', async (_label, query, source) => {
		listUberon.mockResolvedValue({ query: '', total: 0, limit: 25, offset: 0, sort: 'source', source, hits: [] });
		await expect(load({ url: new URL(`https://example.test/repositories/uberon${query}`), fetch: vi.fn() } as never)).resolves.toMatchObject({ initial: { result: { source } } });
	});

	it.each([
		['wrong', { source: null }],
		['missing', { source: undefined }]
	])('fails closed on %s selected-source response echo', async (_label, drift) => {
		listUberon.mockResolvedValue({ query: '', total: 0, limit: 25, offset: 0, sort: 'source', hits: [], ...drift });
		await expect(load({ url: new URL('https://example.test/repositories/uberon?source=cl'), fetch: vi.fn() } as never)).rejects.toMatchObject({ status: 502 });
	});
});
