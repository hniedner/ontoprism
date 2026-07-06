import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import DecompositionPanel from './DecompositionPanel.svelte';
import type { ConceptDecomposition } from '$lib/types';

vi.mock('$lib/api', () => ({ getDecomposition: vi.fn() }));
import { getDecomposition } from '$lib/api';

const mock = vi.mocked(getDecomposition);

beforeEach(() => mock.mockClear());

const decomposed: ConceptDecomposition = {
	code: 'C6135',
	is_legacy_precoordinated: true,
	decomposed_on: '2026-07-06',
	constituents: [
		{
			axis: 'R88',
			axis_label: null,
			filler: 'C27970',
			filler_label: 'Stage III',
			axis_source: 'role',
			most_specific: false
		},
		{
			axis: 'R101',
			axis_label: null,
			filler: 'C12400',
			filler_label: 'Thyroid Gland',
			axis_source: 'role',
			most_specific: true
		}
	]
};

describe('DecompositionPanel', () => {
	it('requests the decomposition for the given code', async () => {
		mock.mockResolvedValue({
			code: 'C3262',
			is_legacy_precoordinated: false,
			decomposed_on: null,
			constituents: []
		});
		render(DecompositionPanel, { code: 'C3262' });
		await screen.findByText(/Not decomposed/);
		expect(mock).toHaveBeenCalledWith('C3262');
	});

	it('renders the legacy badge, axes and filler links for a decomposed concept', async () => {
		mock.mockResolvedValue(decomposed);
		render(DecompositionPanel, { code: 'C6135' });

		expect(await screen.findByText('legacy pre-coordinated')).toBeInTheDocument();
		// Axes shown; fillers link to their own concept pages.
		expect(screen.getByText('R88')).toBeInTheDocument();
		expect(screen.getByText('R101')).toBeInTheDocument();
		const link = screen.getByRole('link', { name: 'Thyroid Gland' });
		expect(link).toHaveAttribute('href', '/repositories/ncit/C12400');
		// The most-specific filler is marked; the non-leaf one is not.
		expect(screen.getByText('leaf')).toBeInTheDocument();
	});

	it('shows a not-decomposed message for an atomic concept', async () => {
		mock.mockResolvedValue({
			code: 'C12400',
			is_legacy_precoordinated: false,
			decomposed_on: null,
			constituents: []
		});
		render(DecompositionPanel, { code: 'C12400' });
		expect(await screen.findByText(/already atomic/)).toBeInTheDocument();
		expect(screen.queryByText('legacy pre-coordinated')).not.toBeInTheDocument();
	});

	it('uses the axis label when present and falls back to the code for an unlabeled filler', async () => {
		mock.mockResolvedValue({
			code: 'C6135',
			is_legacy_precoordinated: true,
			decomposed_on: '2026-07-06',
			constituents: [
				{
					axis: 'op:Morphology',
					axis_label: 'Morphology',
					filler: 'C40384',
					filler_label: null,
					axis_source: 'parent',
					most_specific: false
				}
			]
		});
		render(DecompositionPanel, { code: 'C6135' });
		// axis_label shown (not the raw axis); filler with no label falls back to its code.
		expect(await screen.findByText('Morphology')).toBeInTheDocument();
		expect(screen.getByRole('link', { name: 'C40384' })).toHaveAttribute(
			'href',
			'/repositories/ncit/C40384'
		);
	});
});
