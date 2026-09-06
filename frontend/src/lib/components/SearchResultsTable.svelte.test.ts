import { describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/svelte';
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

	it('labels the active NCIt filter chip with published human-readable names', () => {
		render(SearchResultsTable, {
			hits: [],
			operations: {
				kind: 'server', sort: null, defaultSort: null, activeSortLabel: 'Source order',
				filters: { representation_status: { kind: 'categorical', selected: ['legacy-precoordinated'] } },
				busy: false, onintent: vi.fn()
			}
		});

		const chip = screen.getByRole('button', { name: 'Clear Status filter' });
		expect(chip).toHaveTextContent('Status: Legacy pre-coordinated');
		expect(chip).not.toHaveTextContent(/representation_status|legacy-precoordinated/);
	});

	it('preserves authoritative server order', () => {
		render(SearchResultsTable, { hits });
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

	it('escapes every source-controlled NCIt field', () => {
		const payload = '<img src=x onerror=alert(1)><script>alert(2)</script><svg onload=alert(3)>';
		const { container } = render(SearchResultsTable, {
			hits: [{ ...hits[0], code: payload, label: payload, semantic_type: payload }]
		});
		expect(within(container).getAllByText(payload).length).toBeGreaterThanOrEqual(3);
		expect(container.querySelector('img,script,svg,[onerror],[onload]')).toBeNull();
	});
});
