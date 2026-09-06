import { beforeEach, describe, expect, it, vi } from 'vitest';

const { searchPubmed } = vi.hoisted(() => ({ searchPubmed: vi.fn() }));
vi.mock('$lib/api.pubmed', () => ({ searchPubmed }));

import { load } from './+page.server';

const article = { pmid: '1', title: 'Article', journal: null, pub_date: null, authors: [], doi: null };

function ready(overrides: Record<string, unknown> = {}) {
	return { query: 'cancer', total: 100, limit: 25, offset: 0, sort: 'relevance', articles: [article], ...overrides };
}

describe('PubMed page server grid contract', () => {
	beforeEach(() => searchPubmed.mockReset());

	it('canonicalizes the 10,000-result window before calling the API', async () => {
		await expect(load({
			url: new URL('https://example.test/repositories/pubmed?q=cancer&offset=10000'),
			fetch: vi.fn()
		} as never)).rejects.toMatchObject({ status: 307, location: '/repositories/pubmed?q=cancer&offset=9975' });
		expect(searchPubmed).not.toHaveBeenCalled();
	});

	it('redirects a truthful empty past-total page to the final navigable page', async () => {
		searchPubmed.mockResolvedValue(ready({ total: 57, offset: 75, articles: [] }));
		await expect(load({
			url: new URL('https://example.test/repositories/pubmed?q=cancer&offset=75'),
			fetch: vi.fn()
		} as never)).rejects.toMatchObject({ status: 307, location: '/repositories/pubmed?q=cancer&offset=50' });
	});

	it('maps the closed publication-date sort and rejects response metadata drift', async () => {
		searchPubmed.mockResolvedValue(ready({ sort: 'relevance' }));
		await expect(load({
			url: new URL('https://example.test/repositories/pubmed?q=cancer&sort=pub_date'),
			fetch: vi.fn()
		} as never)).rejects.toMatchObject({ status: 502 });
		expect(searchPubmed).toHaveBeenCalledWith('cancer', 25, 0, 'pub_date', expect.any(Function));
	});
});
