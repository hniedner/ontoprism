import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/svelte';
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
	return screen
		.getAllByRole('row')
		.slice(1) // drop the header row
		.map((r) => r.querySelector('a')?.textContent?.trim() ?? ''); // first col = code link
}

describe('SearchResultsTable', () => {
	it('renders a row per hit with a link to the concept page', () => {
		render(SearchResultsTable, { hits });
		const link = screen.getByRole('link', { name: 'Melanoma' });
		expect(link).toHaveAttribute('href', '/repositories/ncit/C3');
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
});
