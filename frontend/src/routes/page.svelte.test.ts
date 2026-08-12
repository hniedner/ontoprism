import { render, screen, within } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';

import Page from './+page.svelte';

describe('repository landing page', () => {
	it('renders every registered repository with its accessible kind marker', () => {
		render(Page);

		for (const [name, kind] of [
			['NCIt Concepts', 'Local certified proxy'],
			['caDSR CDEs', 'Local certified proxy'],
			['Uberon/CL Concepts', 'Local certified proxy'],
			['ClinicalTrials.gov', 'Remote live service'],
			['PubMed', 'Remote live service']
		] as const) {
			const card = screen.getByRole('link', { name: new RegExp(name) });
			expect(within(card).getByLabelText(kind)).toBeVisible();
		}
	});
});
