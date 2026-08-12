import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import type { UberonConceptDetail } from '$lib/types';
import UberonConceptSummary from './UberonConceptSummary.svelte';

const detail: UberonConceptDetail = {
	code: 'UBERON:0002048',
	source: 'uberon',
	label: 'lung',
	definition: 'An organ.',
	synonyms: ['pulmo'],
	xrefs: ['FMA:7195', 'NCIT:C12468'],
	parents: [],
	children: [],
	relations: [],
	truncated: false
};

describe('UberonConceptSummary', () => {
	it('renders every publisher cross-reference', () => {
		render(UberonConceptSummary, { detail });

		expect(screen.getByText('Cross-references: FMA:7195; NCIT:C12468')).toBeInTheDocument();
	});

	it('uses the code when the source class has no label', () => {
		render(UberonConceptSummary, { detail: { ...detail, label: null } });

		expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('UBERON:0002048');
	});

	it('warns when the concept detail omits relationships at the backend cap', () => {
		render(UberonConceptSummary, { detail: { ...detail, truncated: true } });

		expect(screen.getByRole('status')).toHaveTextContent(
			'This concept has additional relationships beyond the displayed detail limit.'
		);
	});
});
