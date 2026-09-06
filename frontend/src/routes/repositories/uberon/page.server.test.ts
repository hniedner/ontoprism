import { describe, expect, it, vi } from 'vitest';

const { listUberon, searchUberon } = vi.hoisted(() => ({
	listUberon: vi.fn(),
	searchUberon: vi.fn()
}));
vi.mock('$lib/api', () => ({ listUberon, searchUberon }));

import { load } from './+page.server';

describe('Uberon page server sort contract', () => {
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
});
