import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import { within } from '@testing-library/dom';
import DecompositionPanel from './DecompositionPanel.svelte';
import type { ConceptDecomposition } from '$lib/types';

vi.mock('$lib/api', () => ({ getDecomposition: vi.fn(), getConstituentEvidence: vi.fn() }));
import { getDecomposition, getConstituentEvidence } from '$lib/api';

const mock = vi.mocked(getDecomposition);

const decomposed: ConceptDecomposition = {
	code: 'C6135',
	publication_status: 'provisional',
	publication_notice: 'expert review, not an NCIt release',
	outcome: 'decomposed',
	outcome_reason: 'engine emitted 2 constituents',
	review_flags: [
		{ kind: 'needs-review', reason: 'constituent op:PrimarySite / C12400 needs review' }
	],
	is_legacy_precoordinated: true,
	decomposed_on: '2026-07-06',
	constituents: [
		{
			axis: 'R88',
			axis_label: null,
			filler: 'C27970',
			filler_label: 'Stage III',
			axis_source: 'role',
			source_roles: ['R88'],
			most_specific: false,
			axis_ambiguous: true,
			needs_review: false,
			source_group_ids: ['source-stage'],
			normalized_group_id: 'normalized-stage',
			normalized_group_label: 'Stage block',
			source_definition_ids: [],
			upstream: []
		},
		{
			axis: 'R101',
			axis_label: null,
			filler: 'C12400',
			filler_label: 'Thyroid Gland',
			axis_source: 'role',
			source_roles: ['R101'],
			most_specific: true,
			axis_ambiguous: true,
			needs_review: true,
			source_group_ids: ['source-site'],
			normalized_group_id: 'normalized-site',
			normalized_group_label: 'Primary site block',
			source_definition_ids: [],
			upstream: []
		}
	]
};

describe('DecompositionPanel', () => {
    it('marks a proposed filler with the D93 mint flag without source groups', async () => {
        mock.mockResolvedValue({ ...decomposed, constituents: [{ ...decomposed.constituents[0],
            filler: 'MINT-one', source_group_ids: [], normalized_group_id: null, normalized_group_label: null }] });
        render(DecompositionPanel, { code: 'C6135' });
        expect(await screen.findByText('mint-filler: proposed filler')).toBeInTheDocument();
        expect(screen.queryByText(/Source groups:/)).not.toBeInTheDocument();
    });
    it('keeps decomposition visible when evidence fails and does not claim zero support', async () => {
        mock.mockResolvedValue({ ...decomposed, run_id: 'published-run' });
        vi.mocked(getConstituentEvidence).mockRejectedValue(new Error('source unavailable'));
        render(DecompositionPanel, { code: 'C6135' });
        expect(await screen.findByRole('alert')).toHaveTextContent('Source evidence unavailable');
        expect(screen.getByRole('link', { name: 'Thyroid Gland' })).toBeInTheDocument();
        expect(screen.queryByText('Source-backed filler share')).not.toBeInTheDocument();
    });

    it('rejects missing evidence rows instead of reporting zero support', async () => {
        mock.mockResolvedValue({ ...decomposed, run_id: 'published-run' });
        vi.mocked(getConstituentEvidence).mockResolvedValue([]);
        render(DecompositionPanel, { code: 'C6135' });
        expect(await screen.findByRole('alert')).toHaveTextContent('Filler support is not known');
        expect(screen.queryByText('Source-backed filler share')).not.toBeInTheDocument();
    });

    it('shows literal filler support separately from provisional and review status', async () => {
        mock.mockResolvedValue({ ...decomposed, run_id: 'published-run' });
        vi.mocked(getConstituentEvidence).mockResolvedValue([
            { run_id: 'published-run', concept_code: 'C6135', axis: 'R88', filler_code: 'C27970',
              axis_source: 'role', support: 'restriction-backed', policy_choices: ['axis-assignment'],
              inferred_assertions: [], sources: [{ fact_id: 'fact', kind: 'restriction', anchor_code: 'C6135',
                group_id: 'group', depth: 0, role_code: 'R88', filler_code: 'C27970', occurrence_id: 'occurrence', structural_path: [0,1] }] },
            { run_id: 'published-run', concept_code: 'C6135', axis: 'R101', filler_code: 'C12400',
              axis_source: 'role', support: 'not-source-backed', policy_choices: ['collapse'],
              inferred_assertions: ['No exact linked stated filler'], sources: [] }
        ]);
        render(DecompositionPanel, { code: 'C6135' });
        expect(await screen.findByText('Source-backed filler share')).toBeInTheDocument();
        expect(screen.getByText(/literally stated in NCIt.*not.*accepted or correct/)).toBeInTheDocument();
        expect(screen.getByText('restriction-backed: 1/2 (50.0%)')).toBeInTheDocument();
        expect(screen.getByText('genus-backed: 0/2 (0.0%)')).toBeInTheDocument();
        expect(screen.getByText('not-source-backed: 1/2 (50.0%)')).toBeInTheDocument();
        expect(screen.getByText('R88 some C27970')).toBeInTheDocument();
        expect(screen.getByText('No exact linked stated filler')).toBeInTheDocument();
        expect(screen.getAllByText('provisional')).toHaveLength(2);
    });

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
			constituents: []
		});
		vi.useRealTimers();
	});

	it('requests the decomposition for the given code', async () => {
		mock.mockResolvedValue({
			code: 'C3262',
			is_legacy_precoordinated: false,
			decomposed_on: null,
			constituents: []
		});
		render(DecompositionPanel, { code: 'C3262' });
		await screen.findByText('No published decomposition is available.');
		expect(mock).toHaveBeenCalledWith('C3262', undefined, expect.any(AbortSignal));
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
		expect(screen.getByText('expert review, not an NCIt release')).toBeInTheDocument();
		expect(screen.getByText('decomposed')).toBeInTheDocument();
		expect(screen.getByText('engine emitted 2 constituents', { exact: true })).toBeInTheDocument();
		expect(screen.getByText('Needs review')).toBeInTheDocument();
	});

	it.each([
		'decomposed',
		'residual',
		'semantic-excluded',
		'atomic-no-op',
		'unknown'
	] as const)('renders the D93 %s outcome and its reason', async (outcome) => {
		mock.mockResolvedValue({
			...decomposed,
			outcome,
			outcome_reason: `Reason for ${outcome}`,
			constituents: outcome === 'decomposed' ? decomposed.constituents : []
		});
		render(DecompositionPanel, { code: 'C6135' });

		expect(await screen.findByText(outcome)).toBeInTheDocument();
		expect(screen.getByText(`Reason for ${outcome}`, { exact: true })).toBeInTheDocument();
	});

	it('does not infer atomicity from a missing published marker', async () => {
		mock.mockResolvedValue({
			code: 'C12400',
			is_legacy_precoordinated: false,
			decomposed_on: null,
			constituents: []
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
			constituents: [
				{ ...decomposed.constituents[0], filler: 'C27970', filler_label: 'Stage III', normalized_group_id: null, normalized_group_label: null },
				{ ...decomposed.constituents[0], filler: 'C12400', filler_label: 'Thyroid Gland', most_specific: true, normalized_group_id: null, normalized_group_label: null }
			]
		} satisfies ConceptDecomposition);
		render(DecompositionPanel, { code: 'C6135' });
		const group = await screen.findByRole('group', { name: 'R88' });
		expect(within(group).getAllByRole('link')).toHaveLength(2);
		expect(screen.getAllByText('R88')).toHaveLength(1);
	});

	it('keeps distinct labels for the same outside-policy axis separate', async () => {
		mock.mockResolvedValue({
			...decomposed,
			constituents: [
				{
					...decomposed.constituents[0],
					filler: 'C1',
					filler_label: 'First',
					axis_label: 'First reading',
					normalized_group_id: null,
					normalized_group_label: null
				},
				{
					...decomposed.constituents[0],
					filler: 'C2',
					filler_label: 'Second',
					axis_label: 'Second reading',
					normalized_group_id: null,
					normalized_group_label: null
				}
			]
		});

		render(DecompositionPanel, { code: 'C6135' });

		expect(await screen.findByRole('group', { name: 'First reading' })).toBeInTheDocument();
		expect(screen.getByRole('group', { name: 'Second reading' })).toBeInTheDocument();
	});

	it('groups within an axis by normalized policy and exposes separate provenance', async () => {
		mock.mockResolvedValue({
			...decomposed,
			constituents: [
				{ ...decomposed.constituents[0], filler: 'C1', filler_label: 'First', normalized_group_id: 'n1', normalized_group_label: 'Reviewed block', source_group_ids: ['s1'] },
				{ ...decomposed.constituents[0], filler: 'C2', filler_label: 'Second', normalized_group_id: 'n1', normalized_group_label: 'Reviewed block', source_group_ids: ['s2'], axis_ambiguous: true }
			]
		});
		render(DecompositionPanel, { code: 'C6135' });
		expect(await screen.findByText('Reviewed block')).toBeInTheDocument();
		expect(screen.getByText('Source groups: s1')).toBeInTheDocument();
		expect(screen.getByText('Source groups: s2')).toBeInTheDocument();
		expect(screen.getAllByText('Ambiguous axis')).toHaveLength(2);
	});

	it('renders one cross-axis normalized block without erasing axis or pair provenance', async () => {
		const normalizedId = 'a'.repeat(64);
		const stageSystemSource = 'b'.repeat(64);
		const stageValueSource = 'c'.repeat(64);
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
					axis_ambiguous: true,
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
		expect(within(block).getAllByText('Ambiguous axis')).toHaveLength(2);
	});

	it('handles null constituents gracefully', async () => {
		mock.mockResolvedValue({
			code: 'C6135',
			is_legacy_precoordinated: true,
			decomposed_on: '2026-07-06',
			constituents: null
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
			constituents: [
				{
					axis: 'op:Morphology',
					axis_label: 'Morphology',
					filler: 'C40384',
					filler_label: null,
					axis_source: 'parent',
					source_roles: [],
					most_specific: false,
					needs_review: false,
					axis_ambiguous: false,
					source_group_ids: [],
					normalized_group_id: null,
					normalized_group_label: null,
					source_definition_ids: [],
					upstream: []
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
