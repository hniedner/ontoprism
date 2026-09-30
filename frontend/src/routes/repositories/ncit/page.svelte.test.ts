import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/svelte';
import Page from './+page.svelte';
import type { SearchHit } from '$lib/types';

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));
vi.mock('$app/state', () => ({ page: { url: new URL('https://example.test/repositories/ncit') }, navigating: { to: null } }));

function renderPage(hits: SearchHit[], selected: string[] = [], query = '') {
	return render(Page, { params: {}, form: null, data: { domains: { semantic_type: ['Disease or Syndrome', 'Neoplastic Process'] }, initial: {
		query, offset: 0, size: 25, sort: 'source', filters: { representation_status: selected, semantic_type: [] }, textFilters: {},
		result: { query: '', total: hits.length, limit: 25, offset: 0, sort: 'source', representation_status: selected[0] ?? null, column_text: {}, semantic_types: [], hits }
	} } as never });
}

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

describe('NCIt page typed table snippets', () => {
	it('renders a row per hit with a link to the concept page', () => {
		renderPage(hits);
		const link = screen.getByRole('link', { name: 'Melanoma' });
		expect(link).toHaveAttribute('href', '/repositories/ncit/C3');
		expect(document.querySelector('thead')).toHaveClass('sticky', 'top-0', 'bg-card');
		expect(document.querySelector('thead th:first-child')).toHaveClass('sticky', 'bg-card');
		expect(document.querySelector('tbody td:first-child')).toHaveClass('sticky', 'bg-card');
		expect(document.querySelector('tbody td:first-child')).toHaveStyle({ left: '0px' });
	});

	it('shows an accessible legacy badge only for the published marker', () => {
		renderPage(hits);
		expect(screen.getByText('Legacy pre-coordinated')).toBeInTheDocument();
		expect(screen.queryByText(/atomic|not pre-coordinated/i)).not.toBeInTheDocument();
	});

	it('labels the active NCIt filter chip with published human-readable names', () => {
		renderPage([], ['legacy-precoordinated']);

		const chip = screen.getByRole('button', { name: 'Clear Status filter' });
		expect(chip).toHaveTextContent('Status: Legacy pre-coordinated');
		expect(chip).not.toHaveTextContent(/representation_status|legacy-precoordinated/);
	});

	it('preserves authoritative server order', () => {
		renderPage(hits);
		expect(rowCodes()).toEqual(['C3', 'C1']);
	});

	it('offers source-derived Semantic Type multi-select and type-ahead controls', async () => {
		renderPage(hits);
		await fireEvent.click(screen.getByRole('button', { name: 'Filter Semantic type' }));

		expect(screen.getByRole('checkbox', { name: 'Disease or Syndrome' })).toBeInTheDocument();
		expect(screen.getByRole('checkbox', { name: 'Neoplastic Process' })).toBeInTheDocument();
		expect(screen.getByRole('textbox', { name: 'Filter Semantic type text' })).toBeInTheDocument();
	});

	it('renders a dash for a missing label or semantic type', () => {
		renderPage([
				{
					code: 'C9',
					label: null,
					semantic_type: null,
					matched_synonym: null,
					representation_status: null
				}
			]);
		// Label, semantic type, and unassessed status are all explicitly unknown.
		expect(screen.getAllByText('—').length).toBe(3);
	});

	it('identifies an empty repository when no query or filter is active', () => {
		renderPage([]);
		expect(screen.getByText('This repository contains no records.')).toBeInTheDocument();
	});
	it.each(['search', 'filter'])('identifies no matches for an active %s', (mode) => {
		renderPage([], mode === 'filter' ? ['legacy-precoordinated'] : [], mode === 'search' ? 'absent' : '');
		expect(screen.getByText('No records matched the current query and filters.')).toBeInTheDocument();
		expect(screen.queryByText('This repository contains no records.')).not.toBeInTheDocument();
	});

	it('escapes every source-controlled NCIt field', () => {
		const payload = '<img src=x onerror=alert(1)><script>alert(2)</script><svg onload=alert(3)>';
		const { container } = renderPage([{ ...hits[0], code: payload, label: payload, semantic_type: payload }]);
		expect(within(container).getAllByText(payload).length).toBeGreaterThanOrEqual(3);
		expect(container.querySelector('tbody')!.querySelector('img,script,svg,[onerror],[onload]')).toBeNull();
	});
});
