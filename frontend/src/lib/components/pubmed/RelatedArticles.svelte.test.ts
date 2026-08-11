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
		expect(mock).toHaveBeenCalledWith('1', 'similar');
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
