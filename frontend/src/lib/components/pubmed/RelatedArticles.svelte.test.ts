import { render, screen } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import RelatedArticles from './RelatedArticles.svelte';

vi.mock('$lib/api.pubmed', () => ({ getRelatedArticles: vi.fn() }));
import { getRelatedArticles } from '$lib/api.pubmed';

const mock = vi.mocked(getRelatedArticles);

describe('RelatedArticles', () => {
	beforeEach(() => mock.mockClear());

	it('renders related PMID links', async () => {
		mock.mockResolvedValue({ pmid: '1', link_type: 'similar', related_pmids: ['2', '3'] });
		render(RelatedArticles, { pmid: '1' });

		expect(await screen.findByRole('link', { name: '2' })).toHaveAttribute(
			'href',
			'/repositories/pubmed/2'
		);
		expect(mock).toHaveBeenCalledWith('1', 'similar', undefined, expect.any(AbortSignal));
	});

	it('aborts replaced requests and ignores their late success and error', async () => {
		const first = Promise.withResolvers<Awaited<ReturnType<typeof getRelatedArticles>>>();
		const second = Promise.withResolvers<Awaited<ReturnType<typeof getRelatedArticles>>>();
		const third = Promise.withResolvers<Awaited<ReturnType<typeof getRelatedArticles>>>();
		const signals: AbortSignal[] = [];
		for (const request of [first, second, third]) {
			mock.mockImplementationOnce((_pmid, _type, _fetch, signal) => {
				signals.push(signal!);
				return request.promise;
			});
		}

		const view = render(RelatedArticles, { pmid: '1' });
		await vi.waitFor(() => expect(mock).toHaveBeenCalledTimes(1));
		await view.rerender({ pmid: '2' });
		await vi.waitFor(() => expect(mock).toHaveBeenCalledTimes(2));
		await view.rerender({ pmid: '3' });
		await vi.waitFor(() => expect(mock).toHaveBeenCalledTimes(3));
		expect(signals.slice(0, 2).every((signal) => signal.aborted)).toBe(true);

		third.resolve({ pmid: '3', link_type: 'similar', related_pmids: ['NEWEST'] });
		expect(await screen.findByRole('link', { name: 'NEWEST' })).toBeInTheDocument();
		first.resolve({ pmid: '1', link_type: 'similar', related_pmids: ['STALE'] });
		second.reject(new Error('stale failure'));
		await Promise.allSettled([first.promise, second.promise]);
		await Promise.resolve();
		expect(screen.queryByRole('link', { name: 'STALE' })).not.toBeInTheDocument();
		expect(screen.queryByRole('alert')).not.toBeInTheDocument();
	});

	it('distinguishes an unavailable request from a valid empty result', async () => {
		mock.mockResolvedValue({
			pmid: '1',
			link_type: 'similar',
			get related_pmids(): string[] {
				throw new Error('malformed response');
			}
		});
		render(RelatedArticles, { pmid: '1' });

		expect(await screen.findByRole('alert')).toHaveTextContent(
			'Similar articles are unavailable'
		);
		expect(screen.queryByText('No similar articles were returned.')).not.toBeInTheDocument();
	});
});
