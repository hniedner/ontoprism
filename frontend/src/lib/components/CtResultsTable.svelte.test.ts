import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import CtResultsTable from './CtResultsTable.svelte';
import type { CTStudySummary } from '$lib/types';

const studies: CTStudySummary[] = [
	{
		nct_id: 'NCT01',
		title: 'Widgetinib in Melanoma',
		status: 'Recruiting',
		phase: 'Phase 2',
		conditions: ['Melanoma', 'Skin Cancer'],
		interventions: ['Widgetinib'],
		start_date: '2024-01',
		enrollment: 120,
		relevance_score: 0.9
	},
	{
		nct_id: 'NCT02',
		title: 'Observational Cohort',
		status: null,
		phase: null,
		conditions: [],
		interventions: [],
		start_date: null,
		enrollment: null,
		relevance_score: 0.1
	}
];

describe('CtResultsTable', () => {
	it('links each study to its detail page by NCT id', () => {
		render(CtResultsTable, { studies });
		expect(screen.getByRole('link', { name: 'Widgetinib in Melanoma' })).toHaveAttribute(
			'href',
			'/repositories/clinicaltrials/NCT01'
		);
	});

	it('lists the conditions when present', () => {
		render(CtResultsTable, { studies });
		expect(screen.getByText('Melanoma, Skin Cancer')).toBeInTheDocument();
	});

	it('shows the phase chip, or a dash when the study has no phase', () => {
		render(CtResultsTable, { studies });
		expect(screen.getByText('Phase 2')).toBeInTheDocument();
		expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(2);
	});
});
