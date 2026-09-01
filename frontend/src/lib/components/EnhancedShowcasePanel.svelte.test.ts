import { render, screen, waitFor } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';
import { tick } from 'svelte';

vi.mock('$lib/api', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api')>()),
	getEnhancedNcitShowcase: vi.fn()
}));
import { ApiRequestError, getEnhancedNcitShowcase } from '$lib/api';
import EnhancedShowcasePanel from './EnhancedShowcasePanel.svelte';

const mock = vi.mocked(getEnhancedNcitShowcase);

describe('EnhancedShowcasePanel', () => {
	it('visibly separates active, excluded, and unresolved decisions with governance', async () => {
		mock.mockResolvedValue({
			representation: 'enhanced-ncit-showcase',
			banner:
				'Local recoverable showcase; not scientific publication, NCI adoption, equivalence, or production ready.',
			code: 'C6135',
			base_representation_identity: 'a'.repeat(64),
			decision_set_identity: 'b'.repeat(64),
			effective_representation_identity: 'c'.repeat(64),
			base_constituents: [],
			effective_constituents: [
				{ axis: 'op:PrimarySite', filler: 'C12400', label: 'Thyroid Gland' }
			],
			unresolved_visible: [
				{
					candidate_id: 'C6135-P8', axis: 'op:ClinicalFinding', filler: 'C47804',
					label: 'Serum Calcitonin Level Increased', disposition: 'unresolved-visible',
					authority: 'project-provisional', support: ['peer-reviewed-supported'],
					rationale: 'Not universal.', limitations: 'Documented negative cases.',
					source_occurrence_ids: [], group: null
				}
			],
			decisions: [
				{
					candidate_id: 'C6135-P13', axis: 'op:PrimarySite', filler: 'C12400', label: 'Thyroid Gland',
					disposition: 'include', authority: 'source-stated', support: ['source-stated'],
					rationale: 'Source-backed core.', limitations: 'Showcase scope.',
					source_occurrence_ids: ['a'.repeat(64)], group: 'G1'
				},
				{
					candidate_id: 'C6135-P12', axis: 'op:NormalTissueOrigin', filler: 'C33782',
					label: 'Thyroid Gland Follicle', disposition: 'exclude', authority: 'project-provisional',
					support: ['source-stated', 'peer-reviewed-supported', 'project-inference'],
					rationale: 'Conflicting source evidence; C-cell origin.', limitations: 'Local correction.',
					source_occurrence_ids: ['b'.repeat(64)], group: null
				},
				{
					candidate_id: 'C6135-P8', axis: 'op:ClinicalFinding', filler: 'C47804',
					label: 'Serum Calcitonin Level Increased', disposition: 'unresolved-visible',
					authority: 'project-provisional', support: ['peer-reviewed-supported'],
					rationale: 'Not universal.', limitations: 'Documented negative cases.',
					source_occurrence_ids: [], group: null
				}
			]
		});

		render(EnhancedShowcasePanel, { code: 'C6135' });

		await waitFor(() => expect(screen.getByText('Active')).toBeInTheDocument());
		expect(screen.getByText('Excluded')).toBeInTheDocument();
		expect(screen.getByText('Unresolved')).toBeInTheDocument();
		expect(screen.getAllByText('source-stated').length).toBeGreaterThan(0);
		expect(screen.getAllByText('project-provisional').length).toBeGreaterThan(0);
		expect(screen.getAllByText('peer-reviewed-supported').length).toBeGreaterThan(0);
		expect(screen.getByText('Conflicting source evidence; C-cell origin.')).toBeInTheDocument();
		expect(screen.getByText('Documented negative cases.')).toBeInTheDocument();
		expect(screen.getByText(/not scientific publication, NCI adoption, equivalence, or production ready/i)).toBeInTheDocument();
	});

	it('shows that the active showcase is unavailable instead of silently disappearing', async () => {
		mock.mockRejectedValueOnce(new Error('showcase graph unavailable'));

		render(EnhancedShowcasePanel, { code: 'C6135' });

		await waitFor(() =>
			expect(screen.getByText('Enhanced NCIt showcase unavailable')).toBeInTheDocument()
		);
	});

	it('requests every concept and treats an out-of-cohort 404 as no showcase', async () => {
		mock.mockRejectedValueOnce(new ApiRequestError(404, 'outside showcase'));

		render(EnhancedShowcasePanel, { code: 'C999999' });

		await waitFor(() => expect(mock).toHaveBeenCalledWith('C999999', undefined, expect.any(AbortSignal)));
		await tick();
		await tick();
		expect(screen.queryByText('Enhanced NCIt showcase unavailable')).not.toBeInTheDocument();
		expect(screen.queryByText('Enhanced NCIt showcase')).not.toBeInTheDocument();
	});
});
