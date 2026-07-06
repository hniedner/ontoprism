import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import TrialOutcomes from './TrialOutcomes.svelte';
import { trialDetail } from './trial-fixture';

describe('TrialOutcomes', () => {
	it('renders nothing when there are no outcomes or eligibility', () => {
		const { container } = render(TrialOutcomes, { trial: trialDetail() });
		expect(container.textContent?.trim()).toBe('');
	});

	it('labels primary and secondary outcomes distinctly', () => {
		render(TrialOutcomes, {
			trial: trialDetail({
				primary_outcomes: [
					{ measure: 'Overall survival', description: null, time_frame: '5 years' }
				],
				secondary_outcomes: [{ measure: 'Adverse events', description: null, time_frame: null }]
			})
		});
		expect(screen.getByText('Overall survival')).toBeInTheDocument();
		expect(screen.getByText('primary')).toBeInTheDocument();
		expect(screen.getByText('· 5 years')).toBeInTheDocument();
		expect(screen.getByText('Adverse events')).toBeInTheDocument();
		expect(screen.getByText('secondary')).toBeInTheDocument();
	});

	it('renders the eligibility criteria when present', () => {
		render(TrialOutcomes, {
			trial: trialDetail({ eligibility_criteria: 'Age >= 18 years.' })
		});
		expect(screen.getByRole('heading', { name: 'Eligibility' })).toBeInTheDocument();
		expect(screen.getByText('Age >= 18 years.')).toBeInTheDocument();
	});

	it('omits the time-frame annotation for a primary outcome without one', () => {
		render(TrialOutcomes, {
			trial: trialDetail({
				primary_outcomes: [{ measure: 'Response rate', description: null, time_frame: null }]
			})
		});
		expect(screen.getByText('Response rate')).toBeInTheDocument();
		expect(screen.queryByText(/·/)).not.toBeInTheDocument();
	});
});
