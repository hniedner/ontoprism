import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import PubMedResultsTable from './PubMedResultsTable.svelte';
import type { PubMedArticleSummary } from '$lib/types';

const articles: PubMedArticleSummary[] = [
	{
		pmid: '111',
		title: 'Widgetinib in melanoma',
		journal: 'J Onc',
		pub_date: '2024',
		authors: ['Smith J', 'Doe A', 'Roe B', 'Extra C'],
		doi: '10.1/x'
	},
	{
		pmid: '222',
		title: 'No metadata article',
		journal: null,
		pub_date: null,
		authors: [],
		doi: null
	}
];

describe('PubMedResultsTable', () => {
	it('links each article to its detail page by PMID', () => {
		render(PubMedResultsTable, { articles });
		expect(screen.getByRole('link', { name: 'Widgetinib in melanoma' })).toHaveAttribute(
			'href',
			'/repositories/pubmed/111'
		);
	});

	it('shows at most the first three authors', () => {
		render(PubMedResultsTable, { articles });
		expect(screen.getByText('Smith J, Doe A, Roe B')).toBeInTheDocument();
		expect(screen.queryByText(/Extra C/)).not.toBeInTheDocument();
	});

	it('falls back to a dash for a missing journal / date', () => {
		render(PubMedResultsTable, { articles });
		expect(screen.getByText('J Onc')).toBeInTheDocument();
		expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(2);
	});
});
