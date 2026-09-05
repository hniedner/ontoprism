import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/svelte';
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
		expect(screen.getByText('Phase 2')).toBeInTheDocument();
		expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(2);
	});

	it('preserves upstream order until a loaded-page sort and supports condition filters', async () => {
		render(CtResultsTable, { studies });
		expect(Array.from(document.querySelectorAll('tbody tr a')).at(0)).toHaveTextContent('NCT01');
		expect(screen.getByText('Filters and sorting apply only to the trials loaded on this page.')).toBeVisible();
		await fireEvent.click(screen.getByRole('button', { name: 'Sort by Title' }));
		expect(Array.from(document.querySelectorAll('tbody tr a')).at(0)).toHaveTextContent('NCT02');
		await fireEvent.input(screen.getByRole('searchbox', { name: 'Filter loaded trial titles and conditions' }), {
			target: { value: 'skin cancer' }
		});
		expect(document.querySelectorAll('tbody tr')).toHaveLength(1);
	});

	it('escapes every source-controlled clinical-trial summary field', () => {
		const payload = '<img src=x onerror=alert(1)><script>alert(2)</script><svg onload=alert(3)>';
		const { container } = render(CtResultsTable, {
			studies: [{ ...studies[0], nct_id: payload, title: payload, status: payload, phase: payload, conditions: [payload], interventions: [payload], start_date: payload }]
		});
		expect(within(container).getAllByText(payload).length).toBeGreaterThanOrEqual(4);
		expect(container.querySelector('img,script,svg,[onerror],[onload]')).toBeNull();
	});
});
