import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/svelte';
import CdeResultsTable from './CdeResultsTable.svelte';
import type { CdeSummary } from '$lib/types';

const hits: CdeSummary[] = [
	{
		public_id: '100',
		version: '2.0',
		short_name: 'NEOPLASM_HIST',
		long_name: 'Neoplasm Histology',
		context: 'caDSR',
		datatype: 'CHARACTER'
	},
	{
		public_id: '200',
		version: '1.0',
		short_name: 'AGE',
		long_name: 'Patient Age',
		context: null,
		datatype: null
	}
];

describe('CdeResultsTable', () => {
	it('links each CDE to its detail page by public id', () => {
		render(CdeResultsTable, { hits });
		expect(screen.getByRole('link', { name: 'Neoplasm Histology' })).toHaveAttribute(
			'href',
			'/repositories/cadsr/100'
		);
		expect(document.querySelector('thead')).toHaveClass('sticky', 'top-0', 'bg-card');
		expect(document.querySelector('thead th:first-child')).toHaveClass('sticky', 'bg-card');
		expect(document.querySelector('tbody td:first-child')).toHaveClass('sticky', 'bg-card');
		expect(document.querySelector('tbody td:first-child')).toHaveStyle({ left: '0px' });
	});

	it('shows the version and short name alongside the CDE', () => {
		render(CdeResultsTable, { hits });
		expect(screen.getByText('v2.0')).toBeInTheDocument();
		expect(screen.getByText('NEOPLASM_HIST')).toBeInTheDocument();
	});

	it('renders the datatype chip, or a dash when absent', () => {
		render(CdeResultsTable, { hits });
		expect(screen.getByText('CHARACTER')).toBeInTheDocument();
		// The context-less / datatype-less second row falls back to em dashes.
		expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(2);
	});

	it('renders one body row per hit', () => {
		render(CdeResultsTable, { hits });
		expect(document.querySelectorAll('tbody tr')).toHaveLength(2);
	});

	it('omits the short-name annotation when the CDE has none', () => {
		render(CdeResultsTable, {
			hits: [{ ...hits[0], short_name: '' }]
		});
		expect(screen.queryByText('NEOPLASM_HIST')).not.toBeInTheDocument();
	});

	it('handles version being undefined in the each-block key', () => {
		render(CdeResultsTable, {
			hits: [{ ...hits[0], version: undefined as unknown as string }]
		});
		// In Svelte 5, {undefined} in text interpolations renders as empty string.
		expect(screen.getByText(/^v$/)).toBeInTheDocument();
	});

	it('sorts and filters only the loaded CDE page with visible scope disclosure', async () => {
		render(CdeResultsTable, { hits });
		expect(screen.getByText('Filters and sorting apply only to the rows loaded on this page.')).toBeVisible();
		await fireEvent.input(screen.getByRole('searchbox', { name: 'Filter loaded CDE contexts' }), {
			target: { value: ' cadsr ' }
		});
		expect(document.querySelectorAll('tbody tr')).toHaveLength(1);
		await fireEvent.click(screen.getByRole('button', { name: 'Sort by Public ID' }));
		expect(screen.queryByRole('button', { name: /next page/i })).not.toBeInTheDocument();
	});

	it('escapes every source-controlled CDE field', () => {
		const payload = '<img src=x onerror=alert(1)><script>alert(2)</script><svg onload=alert(3)>';
		const { container } = render(CdeResultsTable, {
			hits: [{ public_id: payload, version: payload, short_name: payload, long_name: payload, context: payload, datatype: payload }]
		});
		expect(within(container).getAllByText(payload).length).toBeGreaterThanOrEqual(5);
		expect(container.querySelector('img,script,svg,[onerror],[onload]')).toBeNull();
	});
});
