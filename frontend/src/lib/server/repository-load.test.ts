import { describe, expect, it, vi } from 'vitest';
import { loadRepositoryPage } from './repository-load';

describe('loadRepositoryPage', () => {
	it('uses the search loader for trimmed URL query and strict offset', async () => {
		const search = vi.fn().mockResolvedValue({ total: 1 });
		const list = vi.fn();
		const result = await loadRepositoryPage(
			new URL('http://example.test/repository?q=%20tumor%20&offset=25'),
			search,
			list
		);

		expect(search).toHaveBeenCalledWith('tumor', 25);
		expect(list).not.toHaveBeenCalled();
		expect(result.initial).toEqual({ result: { total: 1 }, query: 'tumor', offset: 25 });
	});

	it('uses the list loader and normalizes malformed offsets', async () => {
		const search = vi.fn();
		const list = vi.fn().mockResolvedValue({ total: 2 });
		const result = await loadRepositoryPage(
			new URL('http://example.test/repository?offset=1junk'),
			search,
			list
		);

		expect(list).toHaveBeenCalledWith(0);
		expect(search).not.toHaveBeenCalled();
		expect(result.initial.query).toBe('');
	});

	it('threads repository-specific URL facets through loaders and initial state', async () => {
		const list = vi.fn().mockResolvedValue({ total: 1 });
		const readUrlState = vi.fn().mockReturnValue({
			representationStatus: 'legacy-precoordinated' as const
		});

		const result = await loadRepositoryPage(
			new URL(
				'http://example.test/repository?representation_status=legacy-precoordinated'
			),
			vi.fn(),
			list,
			readUrlState
		);

		expect(readUrlState).toHaveBeenCalledWith(
			expect.any(URLSearchParams)
		);
		expect(list).toHaveBeenCalledWith(0, {
			representationStatus: 'legacy-precoordinated'
		});
		expect(result.initial).toEqual({
			result: { total: 1 },
			query: '',
			offset: 0,
			representationStatus: 'legacy-precoordinated'
		});
	});
});
