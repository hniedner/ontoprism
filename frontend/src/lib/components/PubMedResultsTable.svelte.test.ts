import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/svelte';
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

	it('preserves upstream order until a loaded-page sort is requested', async () => {
		render(PubMedResultsTable, { articles });
		expect(Array.from(document.querySelectorAll('tbody tr a')).at(0)).toHaveTextContent('111');
		expect(screen.getByText('Filters and sorting apply only to the articles loaded on this page.')).toBeVisible();
		await fireEvent.click(screen.getByRole('button', { name: 'Sort by Title' }));
		expect(Array.from(document.querySelectorAll('tbody tr a')).at(0)).toHaveTextContent('222');
		await fireEvent.input(screen.getByRole('searchbox', { name: 'Filter loaded article journals' }), {
			target: { value: 'j onc' }
		});
		expect(document.querySelectorAll('tbody tr')).toHaveLength(1);
	});

	it('escapes every source-controlled PubMed summary field', () => {
		const payload = '<img src=x onerror=alert(1)><script>alert(2)</script><svg onload=alert(3)>';
		const { container } = render(PubMedResultsTable, {
			articles: [{ pmid: payload, title: payload, journal: payload, pub_date: payload, authors: [payload], doi: payload }]
		});
		expect(within(container).getAllByText(payload).length).toBeGreaterThanOrEqual(4);
		expect(container.querySelector('img,script,svg,[onerror],[onload]')).toBeNull();
	});
});
