import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import PubMedArticleHeader from './PubMedArticleHeader.svelte';
import type { PubMedArticleDetail } from '$lib/types';

const article: PubMedArticleDetail = {
	pmid: '111',
	title: 'Widgetinib in melanoma',
	abstract: 'An abstract.',
	authors: [
		{ last_name: 'Smith', fore_name: 'Jane', initials: 'J' },
		{ last_name: 'Doe', fore_name: null, initials: null }
	],
	journal: 'J Onc',
	pub_date: '2024',
	doi: '10.1/x',
	pmc_id: 'PMC123',
	mesh_terms: [],
	keywords: [],
	url: 'https://pubmed.ncbi.nlm.nih.gov/111/'
};

describe('PubMedArticleHeader', () => {
	it('shows the PMID, journal, pub date and title', () => {
		render(PubMedArticleHeader, { article });
		expect(screen.getByText('PMID 111')).toBeInTheDocument();
		expect(screen.getByText('J Onc')).toBeInTheDocument();
		expect(screen.getByText('· 2024')).toBeInTheDocument();
		expect(screen.getByRole('heading', { name: 'Widgetinib in melanoma' })).toBeInTheDocument();
	});

	it('formats author names, dropping missing name parts', () => {
		render(PubMedArticleHeader, { article });
		// "Jane Smith" (fore+last) and "Doe" (last only, no fore name).
		expect(screen.getByText('Jane Smith, Doe')).toBeInTheDocument();
	});

	it('links out to PubMed and shows the DOI / PMC id', () => {
		render(PubMedArticleHeader, { article });
		expect(screen.getByRole('link', { name: /View on PubMed/ })).toHaveAttribute(
			'href',
			'https://pubmed.ncbi.nlm.nih.gov/111/'
		);
		expect(screen.getByText('DOI: 10.1/x')).toBeInTheDocument();
		expect(screen.getByText('PMC123')).toBeInTheDocument();
	});

	it('omits journal, date, authors, DOI and PMC id when absent', () => {
		render(PubMedArticleHeader, {
			article: {
				...article,
				journal: null,
				pub_date: null,
				authors: [],
				doi: null,
				pmc_id: null
			}
		});
		expect(screen.getByText('PMID 111')).toBeInTheDocument();
		expect(screen.queryByText('J Onc')).not.toBeInTheDocument();
		expect(screen.queryByText(/·/)).not.toBeInTheDocument();
		expect(screen.queryByText(/DOI:/)).not.toBeInTheDocument();
		expect(screen.queryByText('PMC123')).not.toBeInTheDocument();
		// No author line rendered.
		expect(screen.queryByText(/Jane Smith/)).not.toBeInTheDocument();
	});

	it('handles single-name authors (last_name only) in the author list', () => {
		render(PubMedArticleHeader, {
			article: {
				...article,
				authors: [{ last_name: 'Smith', fore_name: null, initials: null }],
				journal: null,
				pub_date: null,
				doi: null,
				pmc_id: null
			}
		});
		expect(screen.getByText('Smith')).toBeInTheDocument();
	});

	it('handles minimal article with only required fields', () => {
		render(PubMedArticleHeader, {
			article: {
				pmid: '1',
				title: 'Minimal',
				abstract: '',
				authors: [],
				journal: null,
				pub_date: null,
				doi: null,
				pmc_id: null,
				mesh_terms: [],
				keywords: [],
				url: 'https://pubmed.ncbi.nlm.nih.gov/1/'
			}
		});
		expect(screen.getByRole('heading', { name: 'Minimal' })).toBeInTheDocument();
		expect(screen.queryByText(/Jane/)).not.toBeInTheDocument();
	});

	it('renders with author having only fore_name', () => {
		render(PubMedArticleHeader, {
			article: {
				...article,
				authors: [{ last_name: null, fore_name: 'John', initials: null }]
			}
		});
		expect(screen.getByText('John')).toBeInTheDocument();
	});

	it('handles null PubMed ID in the template', () => {
		render(PubMedArticleHeader, {
			article: {
				...article,
				pmid: null as unknown as string,
				authors: [],
				journal: null,
				pub_date: null,
				doi: null,
				pmc_id: null
			}
		});
		// PMID text still renders, but without a visible number.
		expect(screen.getByText(/PMID/)).toBeInTheDocument();
	});
});
