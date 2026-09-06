import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/svelte';
import CtResultsTable from './CtResultsTable.svelte';
import { CT_PHASES, type CTStudySummary } from '$lib/types';
import type { DataTableOperations } from './data-table/types';

const studies: CTStudySummary[] = [
	{
		nct_id: 'NCT01',
		title: 'Widgetinib in Melanoma',
		status: 'Recruiting',
		phase: ['NA', 'PHASE2'],
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
		phase: [],
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
		expect(document.querySelector('thead')).toHaveClass('sticky', 'top-0', 'bg-card');
		expect(document.querySelector('thead th:first-child')).toHaveClass('sticky', 'bg-card');
		expect(document.querySelector('tbody td:first-child')).toHaveClass('sticky', 'bg-card');
		expect(document.querySelector('tbody td:first-child')).toHaveStyle({ left: '0px' });
	});

	it('lists the conditions when present', () => {
		render(CtResultsTable, { studies });
		expect(screen.getByText('Melanoma, Skin Cancer')).toBeInTheDocument();
	});

	it('shows the phase chip, or a dash when the study has no phase', () => {
		render(CtResultsTable, { studies });
		expect(within(document.querySelector('tbody') as HTMLElement).getByText('NA, PHASE2')).toBeInTheDocument();
		expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(2);
	});

	it('offers only the canonical filterable phases and never returned-only NA', async () => {
		const operations: DataTableOperations = { kind: 'server', sort: null, defaultSort: null, activeSortLabel: 'Relevance', filters: { status: { kind: 'categorical', selected: [] }, phase: { kind: 'categorical', selected: [] } }, busy: false, onintent: () => {} };
		render(CtResultsTable, { studies, operations });
		await fireEvent.click(screen.getByRole('button', { name: 'Filter Phase' }));
		const phaseGroup = screen.getByRole('group', { name: 'Filter trial phases' });

		for (const phase of CT_PHASES) expect(within(phaseGroup).getByRole('checkbox', { name: phase.replace('_', ' ') })).toBeInTheDocument();
		expect(within(phaseGroup).queryByRole('checkbox', { name: 'NA' })).not.toBeInTheDocument();
	});

	it('preserves upstream order', () => {
		render(CtResultsTable, { studies });
		expect(Array.from(document.querySelectorAll('tbody tr a')).at(0)).toHaveTextContent('NCT01');
	});

	it('escapes every source-controlled clinical-trial summary field', () => {
		const payload = '<img src=x onerror=alert(1)><script>alert(2)</script><svg onload=alert(3)>';
		const { container } = render(CtResultsTable, {
			studies: [{ ...studies[0], nct_id: payload, title: payload, status: payload, phase: ['PHASE1'], conditions: [payload], interventions: [payload], start_date: payload }]
		});
		expect(within(container).getAllByText(payload).length).toBeGreaterThanOrEqual(4);
		expect(container.querySelector('img,script,svg,[onerror],[onload]')).toBeNull();
	});
});
