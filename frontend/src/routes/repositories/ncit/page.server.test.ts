import { beforeEach, describe, expect, it, vi } from 'vitest';

const { listNcit, searchNcit } = vi.hoisted(() => ({ listNcit: vi.fn(), searchNcit: vi.fn() }));
vi.mock('$lib/api', () => ({
	listNcit,
	searchNcit,
	getNcitSemanticTypes: async () => ['Disease or Syndrome', 'Neoplastic Process']
}));

import { load } from './+page.server';

function page(overrides: Record<string, unknown> = {}) {
	return { query: '', total: 0, limit: 25, offset: 0, sort: 'source', representation_status: null, column_text: {}, semantic_types: [], hits: [], ...overrides };
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

	it('fails closed when server returns a different applied column-text filter', async () => {
		listNcit.mockResolvedValue(page({ column_text: {} }));
		await expect(load({
			url: new URL('https://example.test/repositories/ncit?text_label=melanoma'), fetch: vi.fn()
	} as never)).rejects.toMatchObject({ status: 502 });
	});

	it('passes canonical combined column text and categorical state to the backend and accepts its echo', async () => {
		const response = page({ representation_status: 'legacy-precoordinated', column_text: { representation_status: 'legacy', label: 'melanoma' } });
		listNcit.mockResolvedValue(response);
		const fetch = vi.fn();
		const result = await load({
			url: new URL('https://example.test/repositories/ncit?representation_status=legacy-precoordinated&text_label=melanoma&text_representation_status=legacy'), fetch
		} as never);
		expect(listNcit).toHaveBeenCalledWith(expect.objectContaining({
			representationStatus: 'legacy-precoordinated',
			columnText: { label: 'melanoma', representation_status: 'legacy' }, fetch
		}));
		expect(result).toMatchObject({ initial: { result: response } });
	});

	it('passes every selected semantic type and rejects a different response echo', async () => {
		const url = new URL('https://example.test/repositories/ncit?semantic_type=Disease+or+Syndrome&semantic_type=Neoplastic+Process');
		listNcit.mockResolvedValueOnce(page({ semantic_types: ['Disease or Syndrome', 'Neoplastic Process'] }));
		await load({ url, fetch: vi.fn() } as never);
		expect(listNcit).toHaveBeenCalledWith(expect.objectContaining({
			semanticTypes: ['Disease or Syndrome', 'Neoplastic Process']
		}));

		listNcit.mockResolvedValueOnce(page({ semantic_types: ['Neoplastic Process'] }));
		await expect(load({ url, fetch: vi.fn() } as never)).rejects.toMatchObject({ status: 502 });
	});
});
