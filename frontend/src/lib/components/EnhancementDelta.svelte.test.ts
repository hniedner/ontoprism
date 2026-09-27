import { beforeEach, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/svelte';
import EnhancementDelta from './EnhancementDelta.svelte';
import type { DeltaOccurrence } from '$lib/types';

vi.mock('$lib/api', () => ({ getEnhancementDelta: vi.fn() }));
import { getEnhancementDelta } from '$lib/api';
const mock = vi.mocked(getEnhancementDelta);
const occurrence = (category: DeltaOccurrence['category'], id = category): DeltaOccurrence => ({
    walker_max_depth: 7,
    occurrence_id: id, source_fact_id: 'fact', source_group_id: 'group', anchor_code: 'C10',
    depth: 1, structural_path: [0, 1], role_code: 'R101', filler_code: 'C2',
    disposition: null, normalized_axis: null, retained_filler: null, target_exists: false,
    links: [], conservation_category: null, conservation_reason: null, category,
    reason: category === 'not-considered' ? "stated in NCIt; not part of the decomposition's axes" : category
});
beforeEach(() => { mock.mockClear(); });

it('counts every category and links source occurrences and retained collapse targets', async () => {
    mock.mockResolvedValue([
        { ...occurrence('projected'), disposition: 'retained-routed', normalized_axis: 'op:PrimarySite', retained_filler: 'C3' },
        { ...occurrence('represented-through-collapse'), disposition: 'collapsed-r82', normalized_axis: 'op:PrimarySite', retained_filler: 'C4', target_exists: true },
        { ...occurrence('not-projected'), reason: 'missing-disposition' },
        { ...occurrence('not-considered'), role_code: 'R104' },
        { ...occurrence('unclassified'), conservation_reason: 'missing-disposition', links: [{ axis: 'op:PrimarySite', filler_code: 'C2' }] }
    ]);
    render(EnhancementDelta, { runId: 'run-1', code: 'C1' });
    const section = await screen.findByRole('region', { name: 'Stated occurrence delta' });
    for (const label of ['Projected', 'Represented through collapse', 'Not projected', 'Not considered by the decomposition', 'Unclassified']) {
        expect(within(section).getByText(`${label}: 1`)).toBeInTheDocument();
    }
    expect(within(section).getByRole('link', { name: 'op:PrimarySite / C4' })).toHaveAttribute('href', '/repositories/ncit/C4');
    expect(within(section).getAllByRole('link', { name: 'C2' })).toHaveLength(5);
    expect(within(section).getAllByRole('link', { name: 'C10' })).toHaveLength(5);
    expect(within(section).getByText("stated in NCIt; not part of the decomposition's axes")).toBeInTheDocument();
    expect(within(section).getByText('missing-disposition', { exact: true })).toBeInTheDocument();
    expect(within(section).getByText(/"links"/)).toHaveTextContent('missing-disposition');
});

it('does not imply a known-zero delta when no source occurrences were recorded', async () => {
    mock.mockResolvedValue([]);
    render(EnhancementDelta, { runId: 'run-1', code: 'C1' });
    expect(await screen.findByText(/No stated role occurrences were recorded/)).toBeInTheDocument();
    expect(screen.queryByText('Projected: 0')).not.toBeInTheDocument();
});

it('shows known zero not-projected count when recorded occurrences are all represented', async () => {
    mock.mockResolvedValue([occurrence('projected')]);
    render(EnhancementDelta, { runId: 'run-1', code: 'C1' });
    expect(await screen.findByText('No stated role occurrences are classified as not projected.')).toBeInTheDocument();
    expect(screen.getByText('Not projected: 0')).toBeInTheDocument();
});

it('shows unavailable rather than a false empty state on failure', async () => {
    mock.mockRejectedValue(new Error('offline'));
    render(EnhancementDelta, { runId: 'run-1', code: 'C1' });
    expect(await screen.findByRole('alert')).toHaveTextContent('Stated occurrence delta unavailable');
    expect(screen.queryByText('Not projected: 0')).not.toBeInTheDocument();
});

it('clears old rows while loading and ignores replaced requests late success and failure', async () => {
    const first = Promise.withResolvers<DeltaOccurrence[]>();
    const second = Promise.withResolvers<DeltaOccurrence[]>();
    const third = Promise.withResolvers<DeltaOccurrence[]>();
    mock.mockResolvedValueOnce([occurrence('not-projected')]);
    const view = render(EnhancementDelta, { runId: 'run-1', code: 'C1' });
    await screen.findByText('Not projected: 1');
    for (const request of [first, second, third]) mock.mockReturnValueOnce(request.promise);
    await view.rerender({ runId: 'run-1', code: 'C2' });
    expect(screen.queryByText('Not projected: 1')).not.toBeInTheDocument();
    expect(await screen.findByText('Loading stated occurrence delta')).toBeInTheDocument();
    await view.rerender({ runId: 'run-2', code: 'C2' });
    await view.rerender({ runId: 'run-2', code: 'C3' });
    third.resolve([occurrence('projected')]);
    await screen.findByText('Not projected: 0');
    first.resolve([occurrence('not-projected')]);
    second.reject(new Error('stale failure'));
    await Promise.allSettled([first.promise, second.promise]);
    expect(screen.queryByText('Not projected: 1')).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(mock.mock.calls.slice(1, 3).every(call => call[2]?.aborted)).toBe(true);
});
