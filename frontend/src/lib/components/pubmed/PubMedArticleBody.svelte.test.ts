import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import PubMedArticleBody from './PubMedArticleBody.svelte';
import type { PubMedArticleDetail } from '$lib/types';

function article(over: Partial<PubMedArticleDetail> = {}): PubMedArticleDetail {
	return {
		pmid: '111',
		title: 'T',
		abstract: null,
		authors: [],
		journal: null,
		pub_date: null,
		doi: null,
		pmc_id: null,
		mesh_terms: [],
		keywords: [],
		url: 'https://x',
		...over
	};
}

describe('PubMedArticleBody', () => {
	it('renders the abstract when present', () => {
		render(PubMedArticleBody, { article: article({ abstract: 'The abstract text.' }) });
		expect(screen.getByRole('heading', { name: 'Abstract' })).toBeInTheDocument();
		expect(screen.getByText('The abstract text.')).toBeInTheDocument();
	});

	it('omits empty sections', () => {
		render(PubMedArticleBody, { article: article() });
		expect(screen.queryByRole('heading', { name: 'Abstract' })).not.toBeInTheDocument();
		expect(screen.queryByRole('heading', { name: 'MeSH terms' })).not.toBeInTheDocument();
		expect(screen.queryByRole('heading', { name: 'Keywords' })).not.toBeInTheDocument();
	});

	it('renders MeSH terms and keywords when present', () => {
		render(PubMedArticleBody, {
			article: article({
				mesh_terms: [
					{ descriptor: 'Melanoma', qualifiers: [], major_topic: true },
					{ descriptor: 'Humans', qualifiers: [], major_topic: false }
				],
				keywords: ['immunotherapy', 'BRAF']
			})
		});
		expect(screen.getByText('Melanoma')).toBeInTheDocument();
		expect(screen.getByText('Humans')).toBeInTheDocument();
		expect(screen.getByText('immunotherapy, BRAF')).toBeInTheDocument();
	});
});
