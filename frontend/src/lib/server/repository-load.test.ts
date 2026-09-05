import { describe, expect, it, vi } from 'vitest';
import { loadRepositoryPage, parseCursorGridUrl } from './repository-load';

describe('loadRepositoryPage canonical offset state', () => {
	it('threads typed size, aligned offset, sort, and repeated filters through the server query', async () => {
		const search = vi.fn().mockResolvedValue({ total: 80, limit: 50, offset: 50, hits: [] });
		const result = await loadRepositoryPage(
			new URL('http://example.test/repository?q=tumor&size=50&offset=50&sort=name%3Adesc&status=a&status=b'),
			search,
			vi.fn(),
			{ defaultSort: 'source', sorts: ['source', 'name:asc', 'name:desc'], filters: { status: ['a', 'b'] } }
		);
		expect(search).toHaveBeenCalledWith('tumor', { size: 50, offset: 50, sort: 'name:desc', filters: { status: ['a', 'b'] } });
		expect(result.initial.size).toBe(50);
		expect(result.initial.offset).toBe(50);
	});

	it.each([
		['size=24', 'invalid size'], ['size=25&offset=1', 'nonaligned offset'],
		['size=25&sort=bogus', 'invalid sort'], ['size=25&status=bogus', 'invalid filter']
	])('canonicalizes malformed owned browser state once: %s', async (query) => {
		await expect(loadRepositoryPage(
			new URL(`http://example.test/repository?${query}`), vi.fn(),
			vi.fn().mockResolvedValue({ total: 0, limit: 25, offset: 0, hits: [] }),
			{ defaultSort: 'source', sorts: ['source'], filters: { status: ['a'] } }
		)).rejects.toMatchObject({ status: 307, location: '/repository' });
	});

	it('canonicalizes an over-range offset to the final aligned page before rendering', async () => {
		const list = vi.fn().mockResolvedValue({ total: 51, limit: 25, offset: 100, hits: [] });
		await expect(loadRepositoryPage(
			new URL('http://example.test/repository?offset=100'), vi.fn(), list,
			{ defaultSort: 'source', sorts: ['source'], filters: {} }
		)).rejects.toMatchObject({ status: 307, location: '/repository?offset=50' });
	});

	it('fails closed when API metadata does not echo the requested page', async () => {
		await expect(loadRepositoryPage(
			new URL('http://example.test/repository?size=50'), vi.fn(),
			vi.fn().mockResolvedValue({ total: 1, limit: 25, offset: 0, hits: [] }),
			{ defaultSort: 'source', sorts: ['source'], filters: {} }
		)).rejects.toMatchObject({ status: 502 });
	});
});

describe('parseCursorGridUrl', () => {
	it('preserves an opaque cursor trail and repeated categorical filters', () => {
		const parsed = parseCursorGridUrl(
			new URL('http://example.test/repository?q=tumor&cursor=first&cursor=second&phase=PHASE1&phase=PHASE2&status=RECRUITING&status=COMPLETED'),
			{
				filters: {
					status: ['RECRUITING', 'COMPLETED'],
					phase: ['PHASE1', 'PHASE2']
				}
			}
		);
		expect(parsed.cursors).toEqual(['first', 'second']);
		expect(parsed.filters).toEqual({
			status: ['RECRUITING', 'COMPLETED'],
			phase: ['PHASE1', 'PHASE2']
		});
	});
});
