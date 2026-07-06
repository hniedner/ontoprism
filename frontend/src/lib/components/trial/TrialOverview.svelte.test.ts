import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import TrialOverview from './TrialOverview.svelte';
import { trialDetail } from './trial-fixture';

describe('TrialOverview', () => {
	it('renders the NCT id, title and external link', () => {
		render(TrialOverview, {
			trial: trialDetail({ title: 'Widgetinib Study', phase: 'Phase 2', status: 'Recruiting' })
		});
		expect(screen.getByText('NCT01')).toBeInTheDocument();
		expect(screen.getByRole('heading', { name: 'Widgetinib Study' })).toBeInTheDocument();
		expect(screen.getByText('Phase 2')).toBeInTheDocument();
		expect(screen.getByRole('link', { name: /View on ClinicalTrials.gov/ })).toHaveAttribute(
			'href',
			'https://clinicaltrials.gov/study/NCT01'
		);
	});

	it('shows the official title only when it differs from the title', () => {
		const { unmount } = render(TrialOverview, {
			trial: trialDetail({ title: 'Same', official_title: 'Same' })
		});
		expect(screen.queryByText('Same', { selector: 'p' })).not.toBeInTheDocument();
		unmount();

		render(TrialOverview, {
			trial: trialDetail({ title: 'Short', official_title: 'A long official title' })
		});
		expect(screen.getByText('A long official title')).toBeInTheDocument();
	});

	it('falls back to em dashes for missing metadata fields', () => {
		render(TrialOverview, { trial: trialDetail() });
		// study_type, purpose, enrollment, start all absent.
		expect(screen.getAllByText('—').length).toBe(4);
	});
});
