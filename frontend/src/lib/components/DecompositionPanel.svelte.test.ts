import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import { within } from '@testing-library/dom';
import DecompositionPanel from './DecompositionPanel.svelte';
import type { ConceptDecomposition } from '$lib/types';

vi.mock('$lib/api', () => ({ getDecomposition: vi.fn() }));
import { getDecomposition } from '$lib/api';

const mock = vi.mocked(getDecomposition);
const notAccepted = { status: 'not-accepted' } as const;

const decomposed: ConceptDecomposition = {
	code: 'C6135',
	is_legacy_precoordinated: true,
	decomposed_on: '2026-07-06',
	acceptance: notAccepted,
	constituents: [
		{
			axis: 'R88',
			axis_label: null,
			filler: 'C27970',
			filler_label: 'Stage III',
			axis_source: 'role',
			most_specific: false,
			axis_ambiguity_group_id: null,
			source_group_ids: ['source-stage'],
			normalized_group_id: 'normalized-stage',
			normalized_group_label: 'Stage block'
		},
		{
			axis: 'R101',
			axis_label: null,
			filler: 'C12400',
			filler_label: 'Thyroid Gland',
			axis_source: 'role',
			most_specific: true,
			axis_ambiguity_group_id: 'ambiguous-site',
			source_group_ids: ['source-site'],
			normalized_group_id: 'normalized-site',
			normalized_group_label: 'Primary site block'
		}
	]
};

describe('DecompositionPanel', () => {
	it('aborts replaced requests and ignores their late success and error', async () => {
		mock.mockClear();
		const first = Promise.withResolvers<ConceptDecomposition>();
		const second = Promise.withResolvers<ConceptDecomposition>();
		const third = Promise.withResolvers<ConceptDecomposition>();
		const signals: AbortSignal[] = [];
		for (const request of [first, second, third]) {
			mock.mockImplementationOnce((_code, _fetch, signal) => {
				signals.push(signal!);
				return request.promise;
			});
		}

		const view = render(DecompositionPanel, { code: 'C1' });
		await vi.waitFor(() => expect(mock).toHaveBeenCalledTimes(1));
		await view.rerender({ code: 'C2' });
		await vi.waitFor(() => expect(mock).toHaveBeenCalledTimes(2));
		await view.rerender({ code: 'C3' });
		await vi.waitFor(() => expect(mock).toHaveBeenCalledTimes(3));
		expect(signals.slice(0, 2).every((signal) => signal.aborted)).toBe(true);

		third.resolve({ ...decomposed, code: 'C3', constituents: [{ ...decomposed.constituents[0], filler_label: 'Newest filler' }] });
		expect(await screen.findByRole('link', { name: 'Newest filler' })).toBeInTheDocument();
		first.resolve({ ...decomposed, code: 'C1', constituents: [{ ...decomposed.constituents[0], filler_label: 'Stale filler' }] });
		second.reject(new Error('stale failure'));
		await Promise.allSettled([first.promise, second.promise]);
		await Promise.resolve();
		expect(screen.queryByRole('link', { name: 'Stale filler' })).not.toBeInTheDocument();
		expect(screen.queryByText('Decomposition unavailable.')).not.toBeInTheDocument();
	});

	it('shows the loading indicator while waiting for the API', async () => {
		vi.useFakeTimers();
		const { promise, resolve } = Promise.withResolvers<ConceptDecomposition>();
		mock.mockReturnValue(promise);
		render(DecompositionPanel, { code: 'C6135' });
		expect(screen.queryByRole('status')).not.toBeInTheDocument();
		await vi.advanceTimersByTimeAsync(150);
		expect(screen.getByRole('status')).toHaveTextContent('Loading decomposition');
		resolve({
			code: 'C6135',
			is_legacy_precoordinated: false,
			decomposed_on: null,
			constituents: [],
			acceptance: notAccepted
		});
		vi.useRealTimers();
	});

	it('requests the decomposition for the given code', async () => {
		mock.mockResolvedValue({
			code: 'C3262',
			is_legacy_precoordinated: false,
			decomposed_on: null,
			constituents: [],
			acceptance: notAccepted
		});
		render(DecompositionPanel, { code: 'C3262' });
		await screen.findByText('No published decomposition is available.');
		expect(mock).toHaveBeenCalledWith('C3262', undefined, expect.any(AbortSignal));
	});

	it('renders an exclusion warning without hiding retained effective pairs', async () => {
		mock.mockResolvedValue({
			code: 'C198031',
			is_legacy_precoordinated: true,
			decomposed_on: 'run-1',
			constituents: [
				{
					...decomposed.constituents[0],
					axis: 'op:Morphology',
					axis_label: 'Morphology',
					filler: 'C40263',
					filler_label: 'Retained morphology'
				}
			],
			acceptance: {
				status: 'review-required-excluded',
				source_release: '26.07d',
				source_identity: 'a'.repeat(64),
				run_id: 'run-1',
				representation_identity: 'b'.repeat(64),
				publication_identity: 'c'.repeat(64),
				effective_status: 'excluded-from-accepted-effective-projection',
				exclusion_summary: 'Review required — excluded from accepted effective projection',
				official_source_preserved: true
			}
		} as ConceptDecomposition);

		render(DecompositionPanel, { code: 'C198031' });

		expect(
			await screen.findByText('Review required — excluded from accepted effective projection')
		).toBeInTheDocument();
		expect(screen.queryByText('No published decomposition is available.')).not.toBeInTheDocument();
		expect(screen.getByText(/Official NCIt source 26.07d remains accessible/)).toBeInTheDocument();
		expect(screen.getByRole('link', { name: 'Retained morphology' })).toBeInTheDocument();
	});

	it('renders the legacy badge, axes and filler links for a decomposed concept', async () => {
		mock.mockResolvedValue(decomposed);
		render(DecompositionPanel, { code: 'C6135' });

		expect(await screen.findByText('Legacy pre-coordinated')).toBeInTheDocument();
		// Axes shown; fillers link to their own concept pages.
		expect(screen.getByText('R88')).toBeInTheDocument();
		expect(screen.getByText('R101')).toBeInTheDocument();
		const link = screen.getByRole('link', { name: 'Thyroid Gland' });
		expect(link).toHaveAttribute('href', '/repositories/ncit/C12400');
		// The most-specific filler is marked; the non-leaf one is not.
		expect(screen.getByText('leaf')).toBeInTheDocument();
	});

	it('does not infer atomicity from a missing published marker', async () => {
		mock.mockResolvedValue({
			code: 'C12400',
			is_legacy_precoordinated: false,
			decomposed_on: null,
			constituents: [],
			acceptance: notAccepted
		});
		render(DecompositionPanel, { code: 'C12400' });
		expect(await screen.findByText('No published decomposition is available.')).toBeInTheDocument();
		expect(screen.queryByText('Legacy pre-coordinated')).not.toBeInTheDocument();
		expect(screen.queryByText(/atomic|not pre-coordinated/i)).not.toBeInTheDocument();
	});

	it('shows the unavailable state when the fetch fails', async () => {
		mock.mockRejectedValue(new Error('network error'));
		render(DecompositionPanel, { code: 'C6135' });
		expect(await screen.findByText('Decomposition unavailable.')).toBeInTheDocument();
	});

	it('groups multiple fillers under the same axis', async () => {
		mock.mockResolvedValue({
			code: 'C6135',
			is_legacy_precoordinated: true,
			decomposed_on: '2026-07-06',
			acceptance: notAccepted,
			constituents: [
				{ axis: 'R88', axis_label: null, filler: 'C27970', filler_label: 'Stage III', axis_source: 'role', most_specific: false, axis_ambiguity_group_id: null, source_group_ids: [], normalized_group_id: null, normalized_group_label: null },
				{ axis: 'R88', axis_label: null, filler: 'C12400', filler_label: 'Thyroid Gland', axis_source: 'role', most_specific: true, axis_ambiguity_group_id: null, source_group_ids: [], normalized_group_id: null, normalized_group_label: null }
			]
		} satisfies ConceptDecomposition);
		render(DecompositionPanel, { code: 'C6135' });
		expect(await screen.findAllByText('R88')).toHaveLength(2);
	});

	it('groups within an axis by normalized policy and exposes separate provenance', async () => {
		mock.mockResolvedValue({
			...decomposed,
			constituents: [
				{ ...decomposed.constituents[0], filler: 'C1', filler_label: 'First', normalized_group_id: 'n1', normalized_group_label: 'Reviewed block', source_group_ids: ['s1'] },
				{ ...decomposed.constituents[0], filler: 'C2', filler_label: 'Second', normalized_group_id: 'n1', normalized_group_label: 'Reviewed block', source_group_ids: ['s2'], axis_ambiguity_group_id: 'a1' }
			]
		});
		render(DecompositionPanel, { code: 'C6135' });
		expect(await screen.findByText('Reviewed block')).toBeInTheDocument();
		expect(screen.getByText('Source groups: s1')).toBeInTheDocument();
		expect(screen.getByText('Source groups: s2')).toBeInTheDocument();
		expect(screen.getByText('Axis ambiguity: a1')).toBeInTheDocument();
	});

	it('renders one cross-axis normalized block without erasing axis or pair provenance', async () => {
		const normalizedId = 'a'.repeat(64);
		const stageSystemSource = 'b'.repeat(64);
		const stageValueSource = 'c'.repeat(64);
		const ambiguityId = 'd'.repeat(64);
		mock.mockResolvedValue({
			...decomposed,
			constituents: [
				{
					...decomposed.constituents[0],
					axis: 'op:StageSystem',
					axis_label: 'Stage System',
					filler: 'C90530',
					filler_label: 'AJCC 8th Edition',
					source_group_ids: [stageSystemSource],
					normalized_group_id: normalizedId,
					normalized_group_label: 'Reviewed stage assessment'
				},
				{
					...decomposed.constituents[0],
					axis: 'op:StageValue',
					axis_label: 'Stage Value',
					filler: 'C27966',
					filler_label: 'Stage II',
					axis_ambiguity_group_id: ambiguityId,
					source_group_ids: [stageValueSource],
					normalized_group_id: normalizedId,
					normalized_group_label: 'Reviewed stage assessment'
				}
			]
		});

		render(DecompositionPanel, { code: 'C6135' });

		const block = await screen.findByRole('group', { name: 'Reviewed stage assessment' });
		expect(screen.getAllByText('Reviewed stage assessment')).toHaveLength(1);
		expect(within(block).getByText('Stage System')).toBeInTheDocument();
		expect(within(block).getByText('Stage Value')).toBeInTheDocument();
		expect(within(block).getByRole('link', { name: 'AJCC 8th Edition' })).toBeInTheDocument();
		expect(within(block).getByRole('link', { name: 'Stage II' })).toBeInTheDocument();
		expect(within(block).getByText(`Source groups: ${stageSystemSource}`)).toBeInTheDocument();
		expect(within(block).getByText(`Source groups: ${stageValueSource}`)).toBeInTheDocument();
		expect(within(block).getByText(`Axis ambiguity: ${ambiguityId}`)).toBeInTheDocument();
	});

	it('handles null constituents gracefully', async () => {
		mock.mockResolvedValue({
			code: 'C6135',
			is_legacy_precoordinated: true,
			decomposed_on: '2026-07-06',
			constituents: null,
			acceptance: notAccepted
		} as unknown as ConceptDecomposition);
		render(DecompositionPanel, { code: 'C6135' });
		// Legacy badge shown, null constituents treated as empty list → no error.
		expect(await screen.findByText('Legacy pre-coordinated')).toBeInTheDocument();
	});

	it('uses the axis label when present and falls back to the code for an unlabeled filler', async () => {
		mock.mockResolvedValue({
			code: 'C6135',
			is_legacy_precoordinated: true,
			decomposed_on: '2026-07-06',
			acceptance: notAccepted,
			constituents: [
				{
					axis: 'op:Morphology',
					axis_label: 'Morphology',
					filler: 'C40384',
					filler_label: null,
					axis_source: 'parent',
					most_specific: false,
					axis_ambiguity_group_id: null,
					source_group_ids: [],
					normalized_group_id: null,
					normalized_group_label: null
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
