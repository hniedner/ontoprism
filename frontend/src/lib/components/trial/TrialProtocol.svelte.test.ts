import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import TrialProtocol from './TrialProtocol.svelte';
import { trialDetail } from './trial-fixture';

describe('TrialProtocol', () => {
	it('renders nothing without conditions or interventions', () => {
		const { container } = render(TrialProtocol, { trial: trialDetail() });
		expect(container.textContent?.trim()).toBe('');
	});

	it('lists conditions as chips', () => {
		render(TrialProtocol, { trial: trialDetail({ conditions: ['Melanoma', 'Skin Cancer'] }) });
		expect(screen.getByRole('heading', { name: 'Conditions' })).toBeInTheDocument();
		expect(screen.getByText('Melanoma')).toBeInTheDocument();
		expect(screen.getByText('Skin Cancer')).toBeInTheDocument();
	});

	it('lists interventions with type and description', () => {
		render(TrialProtocol, {
			trial: trialDetail({
				interventions: [
					{ name: 'Widgetinib', type: 'Drug', description: 'A kinase inhibitor.' }
				]
			})
		});
		expect(screen.getByText('Widgetinib')).toBeInTheDocument();
		expect(screen.getByText('Drug')).toBeInTheDocument();
		expect(screen.getByText('A kinase inhibitor.')).toBeInTheDocument();
	});

	it('renders a bare intervention without type or description', () => {
		render(TrialProtocol, {
			trial: trialDetail({ interventions: [{ name: 'Placebo', type: null, description: null }] })
		});
		expect(screen.getByText('Placebo')).toBeInTheDocument();
	});
});
