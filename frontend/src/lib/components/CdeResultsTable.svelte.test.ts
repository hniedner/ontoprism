import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/svelte';
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
		expect(screen.getAllByRole('row').slice(1)).toHaveLength(2);
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
});
