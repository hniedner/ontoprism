import { render, screen } from '@testing-library/svelte';
import { expect, it, vi } from 'vitest';
import FillerSupportSummary from './FillerSupportSummary.svelte';
import type { ConstituentEvidence } from '$lib/types';

it('accounts for P334-backed fillers in the support denominator', () => {
    const evidence: ConstituentEvidence[] = [{
        run_id: 'run', concept_code: 'C1', axis: 'op:HistologyAnchor',
        filler_code: 'C2', axis_source: 'p334', support: 'p334-backed',
        sources: [], policy_choices: [], inferred_assertions: []
    }];
    render(FillerSupportSummary, { evidence, failed: false, runId: 'run' });
    expect(screen.getByText('p334-backed: 1/1 (100.0%)')).toBeInTheDocument();
    expect(screen.getByText('not-source-backed: 0/1 (0.0%)')).toBeInTheDocument();
});

it('reports pending evidence rather than claiming no source support', async () => {
    vi.useFakeTimers();
    render(FillerSupportSummary, { evidence: null, failed: false, runId: 'run' });
    await vi.advanceTimersByTimeAsync(200);
    expect(screen.getByRole('status')).toHaveTextContent('Loading source evidence');
    expect(screen.queryByText('Source-backed filler share')).not.toBeInTheDocument();
    vi.useRealTimers();
});
