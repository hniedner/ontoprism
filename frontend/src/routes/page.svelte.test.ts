import { render, screen, within } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';

import Page from './+page.svelte';

describe('repository landing page', () => {
	it('presents only the current exploration capabilities', () => {
		render(Page);

		expect(screen.getByRole('heading', { level: 1, name: 'Current ontology capabilities' })).toBeVisible();
		expect(screen.getByText(/Search, browse, and cross-navigate certified NCIt/)).toBeVisible();
		expect(document.body).not.toHaveTextContent(
			/generic adapter|ontology editing|generic reasoning|AI authoring|release reconciliation/i
		);
	});

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
