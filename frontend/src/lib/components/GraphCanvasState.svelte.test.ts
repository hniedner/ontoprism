import { render, screen } from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import GraphCanvasState from './GraphCanvasState.svelte';

afterEach(() => vi.useRealTimers());

describe('GraphCanvasState', () => {
	it('gives loading precedence over error and empty overlays', async () => {
		vi.useFakeTimers();
		render(GraphCanvasState, {
			loading: true,
			error: 'store unavailable',
			visibleNodeCount: 0,
			expanding: false,
			truncated: false
		});

		await vi.advanceTimersByTimeAsync(150);
		expect(screen.getByRole('status')).toHaveTextContent('Building graph');
		expect(screen.queryByText('store unavailable')).not.toBeInTheDocument();
		expect(screen.queryByText('No graph nodes match the active filters.')).not.toBeInTheDocument();
	});

	it('renders an error after loading completes', () => {
		render(GraphCanvasState, {
			loading: false,
			error: 'store unavailable',
			visibleNodeCount: 0,
			expanding: false,
			truncated: false
		});

		expect(screen.getByText('store unavailable')).toBeInTheDocument();
		expect(screen.queryByText('No graph nodes match the active filters.')).not.toBeInTheDocument();
	});

	it('renders the true empty state only when no visible nodes remain', () => {
		render(GraphCanvasState, {
			loading: false,
			error: null,
			visibleNodeCount: 0,
			expanding: false,
			truncated: false
		});

		expect(screen.getByText('No graph nodes match the active filters.')).toBeInTheDocument();
	});

	it('renders the delayed expansion status independently', async () => {
		vi.useFakeTimers();
		render(GraphCanvasState, {
			loading: false,
			error: null,
			visibleNodeCount: 2,
			expanding: true,
			truncated: false
		});

		await vi.advanceTimersByTimeAsync(150);
		expect(screen.getByRole('status')).toHaveTextContent('Expanding graph');
		expect(screen.queryByText('No graph nodes match the active filters.')).not.toBeInTheDocument();
	});

	it('warns when the displayed graph omits relationships at the backend cap', () => {
		render(GraphCanvasState, {
			loading: false,
			error: null,
			visibleNodeCount: 2,
			expanding: false,
			truncated: true
		});

		expect(screen.getByRole('status')).toHaveTextContent(
			'This graph is partial because the relationship limit was reached.'
		);
	});
});
