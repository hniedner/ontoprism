import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import TrialSupport from './TrialSupport.svelte';
import { trialDetail } from './trial-fixture';

describe('TrialSupport', () => {
	it('renders nothing without sponsors or references', () => {
		const { container } = render(TrialSupport, { trial: trialDetail() });
		expect(container.textContent?.trim()).toBe('');
	});

	it('lists sponsors with their role', () => {
		render(TrialSupport, {
			trial: trialDetail({ sponsors: [{ name: 'NCI', role: 'Lead' }] })
		});
		expect(screen.getByRole('heading', { name: 'Sponsors' })).toBeInTheDocument();
		expect(screen.getByText('NCI')).toBeInTheDocument();
		expect(screen.getByText('Lead')).toBeInTheDocument();
	});

	it('lists references with their PMID', () => {
		render(TrialSupport, {
			trial: trialDetail({
				references: [{ pmid: '999', citation: 'Smith et al. 2024', reference_type: 'result' }]
			})
		});
		expect(screen.getByText('Smith et al. 2024')).toBeInTheDocument();
		expect(screen.getByText('PMID 999')).toBeInTheDocument();
	});

	it('renders a sponsor without a role and a reference without a PMID', () => {
		render(TrialSupport, {
			trial: trialDetail({
				sponsors: [{ name: 'Acme', role: null }],
				references: [{ pmid: null, citation: 'Anon 2020', reference_type: null }]
			})
		});
		expect(screen.getByText('Acme')).toBeInTheDocument();
		expect(screen.getByText('Anon 2020')).toBeInTheDocument();
		expect(screen.queryByText(/PMID/)).not.toBeInTheDocument();
	});
});
