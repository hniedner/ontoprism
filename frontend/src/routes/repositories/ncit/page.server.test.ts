import { beforeEach, describe, expect, it, vi } from 'vitest';

const { listNcit, searchNcit } = vi.hoisted(() => ({ listNcit: vi.fn(), searchNcit: vi.fn() }));
vi.mock('$lib/api', () => ({ listNcit, searchNcit }));

import { load } from './+page.server';

function page(overrides: Record<string, unknown> = {}) {
	return { query: '', total: 0, limit: 25, offset: 0, sort: 'source', representation_status: null, hits: [], ...overrides };
}

describe('NCIt page server filter echo contract', () => {
	beforeEach(() => {
		listNcit.mockReset();
		searchNcit.mockReset();
	});

	it('accepts explicit canonical filtered and unfiltered echoes', async () => {
		listNcit.mockResolvedValueOnce(page({ representation_status: 'legacy-precoordinated' }));
		await expect(load({ url: new URL('https://example.test/repositories/ncit?representation_status=legacy-precoordinated'), fetch: vi.fn() } as never)).resolves.toMatchObject({ initial: { result: { representation_status: 'legacy-precoordinated' } } });

		listNcit.mockResolvedValueOnce(page());
		await expect(load({ url: new URL('https://example.test/repositories/ncit'), fetch: vi.fn() } as never)).resolves.toMatchObject({ initial: { result: { representation_status: null } } });
	});

	it.each([
		['wrong', { representation_status: null }],
		['missing', { representation_status: undefined }]
	])('fails closed on %s filtered response echo', async (_label, drift) => {
		listNcit.mockResolvedValue(page(drift));
		await expect(load({ url: new URL('https://example.test/repositories/ncit?representation_status=legacy-precoordinated'), fetch: vi.fn() } as never)).rejects.toMatchObject({ status: 502 });
	});
});
