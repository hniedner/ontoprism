import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import type { ConceptDetail } from '$lib/types';
import NcitConceptSummary from './NcitConceptSummary.svelte';

const detail: ConceptDetail = {
	code: 'C3262',
	label: 'Neoplasm',
	preferred_name: 'Neoplasm',
	definition: 'A growth.',
	representation_status: 'legacy-precoordinated',
	semantic_types: ['Neoplastic Process'],
	synonyms: [],
	parents: [],
	children: [],
	roles: [],
	associations: [],
	incoming_roles: []
};

describe('NcitConceptSummary', () => {
	it('renders the shared accessible badge for the published legacy marker', () => {
		render(NcitConceptSummary, { detail });

		expect(screen.getByText('Legacy pre-coordinated')).toBeInTheDocument();
	});

	it('does not infer atomicity when the status is unassessed', () => {
		render(NcitConceptSummary, {
			detail: { ...detail, representation_status: null }
		});

		expect(screen.queryByText('Legacy pre-coordinated')).not.toBeInTheDocument();
		expect(screen.queryByText(/atomic|not pre-coordinated/i)).not.toBeInTheDocument();
	});
});
