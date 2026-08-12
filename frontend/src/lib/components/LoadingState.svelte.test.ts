import { cleanup, render, screen } from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import LoadingState from './LoadingState.svelte';

afterEach(() => {
	cleanup();
	vi.useRealTimers();
});

describe('LoadingState', () => {
	it('reserves stable space but delays the indeterminate indicator', async () => {
		vi.useFakeTimers();
		const { container } = render(LoadingState, {
			active: true,
			label: 'Loading concept graph',
			delayMs: 150,
			minHeight: '12rem'
		});

		expect((container.firstElementChild as HTMLElement).style.minHeight).toBe('12rem');
		expect(screen.queryByRole('status')).not.toBeInTheDocument();
		await vi.advanceTimersByTimeAsync(149);
		expect(screen.queryByRole('status')).not.toBeInTheDocument();
		await vi.advanceTimersByTimeAsync(1);
		expect(screen.getByRole('status')).toHaveTextContent('Loading concept graph');
	});

	it('never flashes when loading completes before the threshold', async () => {
		vi.useFakeTimers();
		const view = render(LoadingState, { active: true, label: 'Loading results', delayMs: 150 });
		await vi.advanceTimersByTimeAsync(80);
		await view.rerender({ active: false, label: 'Loading results', delayMs: 150 });
		await vi.advanceTimersByTimeAsync(100);
		expect(screen.queryByRole('status')).not.toBeInTheDocument();
	});
});
