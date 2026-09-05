import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/svelte';
import { tick } from 'svelte';
import SearchResultsTable from './SearchResultsTable.svelte';
import type { SearchHit } from '$lib/types';

const hits: SearchHit[] = [
	{
		code: 'C3',
		label: 'Melanoma',
		semantic_type: 'Neoplastic Process',
		matched_synonym: null,
		representation_status: 'legacy-precoordinated'
	},
	{
		code: 'C1',
		label: 'Adenoma',
		semantic_type: 'Neoplastic Process',
		matched_synonym: null,
		representation_status: null
	}
];

function rowCodes(): string[] {
	return Array.from(document.querySelectorAll('tbody tr'))
		.map((r) => r.querySelector('a')?.textContent?.trim() ?? ''); // first col = code link
}

describe('SearchResultsTable', () => {
	it('renders a row per hit with a link to the concept page', () => {
		render(SearchResultsTable, { hits });
		const link = screen.getByRole('link', { name: 'Melanoma' });
		expect(link).toHaveAttribute('href', '/repositories/ncit/C3');
		expect(document.querySelector('thead')).toHaveClass('sticky', 'top-0', 'bg-card');
		expect(document.querySelector('thead th:first-child')).toHaveClass('sticky', 'bg-card');
		expect(document.querySelector('tbody td:first-child')).toHaveClass('sticky', 'bg-card');
		expect(document.querySelector('tbody td:first-child')).toHaveStyle({ left: '0px' });
	});

	it('shows an accessible legacy badge only for the published marker', () => {
		render(SearchResultsTable, { hits });
		expect(screen.getByText('Legacy pre-coordinated')).toBeInTheDocument();
		expect(screen.queryByText(/atomic|not pre-coordinated/i)).not.toBeInTheDocument();
	});

	it('sorts by label ascending by default (Adenoma/C1 before Melanoma/C3)', () => {
		render(SearchResultsTable, { hits });
		expect(rowCodes()).toEqual(['C1', 'C3']);
	});

	it('reverses the order when the sorted column header is clicked', async () => {
		render(SearchResultsTable, { hits });
		screen.getByRole('button', { name: /Name/i }).click();
		await tick();
		expect(rowCodes()).toEqual(['C3', 'C1']);
	});

	it('switches the sort column (ascending) when a different header is clicked', async () => {
		render(SearchResultsTable, { hits });
		// Sort by Code ascending: C1 before C3.
		screen.getByRole('button', { name: /Code/i }).click();
		await tick();
		expect(rowCodes()).toEqual(['C1', 'C3']);
	});

	it('sorts by semantic type when that header is clicked', async () => {
		const mixed: SearchHit[] = [
			{
				code: 'C3',
				label: 'Zeta',
				semantic_type: 'Anatomic Structure',
				matched_synonym: null,
				representation_status: null
			},
			{
				code: 'C1',
				label: 'Alpha',
				semantic_type: 'Neoplastic Process',
				matched_synonym: null,
				representation_status: null
			}
		];
		render(SearchResultsTable, { hits: mixed });
		screen.getByRole('button', { name: /Semantic type/i }).click();
		await tick();
		// "Anatomic Structure" (C3) sorts before "Neoplastic Process" (C1).
		expect(rowCodes()).toEqual(['C3', 'C1']);
	});

	it('renders a dash for a missing label or semantic type', () => {
		render(SearchResultsTable, {
			hits: [
				{
					code: 'C9',
					label: null,
					semantic_type: null,
					matched_synonym: null,
					representation_status: null
				}
			]
		});
		// Label, semantic type, and unassessed status are all explicitly unknown.
		expect(screen.getAllByText('—').length).toBe(3);
	});

	it('shows a no-results message for an empty hit list', () => {
		render(SearchResultsTable, { hits: [] });
		expect(screen.getByText('No results.')).toBeInTheDocument();
	});

	it('discloses and applies page-local filters without adding pagination', async () => {
		render(SearchResultsTable, { hits });
		expect(screen.getByText('Filters and sorting apply only to the rows loaded on this page.')).toBeVisible();
		await fireEvent.input(screen.getByRole('searchbox', { name: 'Filter loaded NCIt names' }), {
			target: { value: ' melanoma ' }
		});
		expect(rowCodes()).toEqual(['C3']);
		expect(screen.queryByRole('button', { name: /next page/i })).not.toBeInTheDocument();
	});

	it('escapes every source-controlled NCIt field', () => {
		const payload = '<img src=x onerror=alert(1)><script>alert(2)</script><svg onload=alert(3)>';
		const { container } = render(SearchResultsTable, {
			hits: [{ ...hits[0], code: payload, label: payload, semantic_type: payload }]
		});
		expect(within(container).getAllByText(payload).length).toBeGreaterThanOrEqual(3);
		expect(container.querySelector('img,script,svg,[onerror],[onload]')).toBeNull();
	});
});
