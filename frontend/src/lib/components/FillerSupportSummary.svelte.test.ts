import { render, screen } from '@testing-library/svelte';
import { expect, it, vi } from 'vitest';
import FillerSupportSummary from './FillerSupportSummary.svelte';

it('reports pending evidence rather than claiming no source support', async () => {
    vi.useFakeTimers();
    render(FillerSupportSummary, { evidence: null, failed: false, runId: 'run' });
    await vi.advanceTimersByTimeAsync(200);
    expect(screen.getByRole('status')).toHaveTextContent('Loading source evidence');
    expect(screen.queryByText('Source-backed filler share')).not.toBeInTheDocument();
    vi.useRealTimers();
});
