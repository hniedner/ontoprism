import { describe, expect, it, vi } from 'vitest';
import { createRepoBrowse } from './repo-browse.svelte';

interface Page {
	total: number;
	hits: string[];
}

function page(total: number, hits: string[]): Page {
	return { total, hits };
}

describe('createRepoBrowse', () => {
	it('loads browse mode (list) for an empty term and search mode for a non-empty term', async () => {
		const searchFn = vi.fn().mockResolvedValue(page(2, ['s1', 's2']));
		const listFn = vi.fn().mockResolvedValue(page(9, ['l1']));
		const browse = createRepoBrowse<Page>(searchFn, listFn);

		await browse.load(0, '');
		expect(browse.mode).toBe('browse');
		expect(listFn).toHaveBeenCalledOnce();
		expect(browse.result?.total).toBe(9);

		await browse.load(0, '  melanoma  ');
		expect(browse.mode).toBe('search');
		expect(searchFn).toHaveBeenCalledWith('melanoma', { limit: 25, offset: 0 });
		expect(browse.submitted).toBe('melanoma');
	});

	it('search() resets the offset to 0 while load() preserves the passed offset', async () => {
		const searchFn = vi.fn().mockResolvedValue(page(100, ['x']));
		const listFn = vi.fn().mockResolvedValue(page(0, []));
		const browse = createRepoBrowse<Page>(searchFn, listFn);

		await browse.load(50, 'tumor');
		expect(browse.offset).toBe(50);

		browse.q = 'tumor';
		browse.search();
		await vi.waitFor(() => expect(browse.offset).toBe(0));
		expect(searchFn).toHaveBeenLastCalledWith('tumor', { limit: 25, offset: 0 });
	});

	it('captures the error message and clears the result on failure', async () => {
		const searchFn = vi.fn().mockRejectedValue(new Error('boom'));
		const listFn = vi.fn().mockResolvedValue(page(0, []));
		const browse = createRepoBrowse<Page>(searchFn, listFn);

		await browse.load(0, 'x');
		expect(browse.error).toBe('boom');
		expect(browse.result).toBeNull();
		expect(browse.loading).toBe(false);
	});

	it('suggest() sets the query and runs a search', async () => {
		const searchFn = vi.fn().mockResolvedValue(page(1, ['a']));
		const listFn = vi.fn().mockResolvedValue(page(0, []));
		const browse = createRepoBrowse<Page>(searchFn, listFn);

		browse.suggest('immunotherapy');
		await vi.waitFor(() => expect(browse.result?.total).toBe(1));
		expect(browse.q).toBe('immunotherapy');
		expect(searchFn).toHaveBeenCalledWith('immunotherapy', { limit: 25, offset: 0 });
	});

	it('search-only mode (no listFn): an empty term is a no-op, a term searches', async () => {
		const searchFn = vi.fn().mockResolvedValue(page(3, ['x']));
		const browse = createRepoBrowse<Page>(searchFn); // no listFn

		await browse.load(0, '   '); // empty → no-op
		expect(searchFn).not.toHaveBeenCalled();
		expect(browse.result).toBeNull();

		await browse.load(0, 'melanoma');
		expect(searchFn).toHaveBeenCalledOnce();
		expect(browse.result?.total).toBe(3);
	});
});
