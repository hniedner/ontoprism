import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import SimilarConcepts from './SimilarConcepts.svelte';
import type { SimilarConcept } from '$lib/types';

vi.mock('$lib/api', () => ({ similarConcepts: vi.fn() }));
import { similarConcepts } from '$lib/api';

const mock = vi.mocked(similarConcepts);

describe('SimilarConcepts', () => {
	it('aborts replaced requests and ignores their late success and error', async () => {
		mock.mockClear();
		const first = Promise.withResolvers<SimilarConcept[]>();
		const second = Promise.withResolvers<SimilarConcept[]>();
		const third = Promise.withResolvers<SimilarConcept[]>();
		const signals: AbortSignal[] = [];
		for (const request of [first, second, third]) {
			mock.mockImplementationOnce((_code, _limit, _fetch, signal) => {
				signals.push(signal!);
				return request.promise;
			});
		}

		const view = render(SimilarConcepts, { code: 'C1' });
		await vi.waitFor(() => expect(mock).toHaveBeenCalledTimes(1));
		await view.rerender({ code: 'C2' });
		await vi.waitFor(() => expect(mock).toHaveBeenCalledTimes(2));
		await view.rerender({ code: 'C3' });
		await vi.waitFor(() => expect(mock).toHaveBeenCalledTimes(3));
		expect(signals.slice(0, 2).every((signal) => signal.aborted)).toBe(true);

		third.resolve([{ code: 'C30', label: 'Newest concept', score: 0.9 }]);
		expect(await screen.findByRole('link', { name: 'Newest concept' })).toBeInTheDocument();
		first.resolve([{ code: 'C10', label: 'Stale concept', score: 0.8 }]);
		second.reject(new Error('stale failure'));
		await Promise.allSettled([first.promise, second.promise]);
		await Promise.resolve();
		expect(screen.queryByText('Stale concept')).not.toBeInTheDocument();
		expect(screen.queryByText('Embeddings unavailable.')).not.toBeInTheDocument();
	});

	it('requests the top-10 similar concepts for the given code', async () => {
		mock.mockResolvedValue([]);
		render(SimilarConcepts, { code: 'C3262' });
		await screen.findByText('None.');
		expect(mock).toHaveBeenCalledWith('C3262', 10, undefined, expect.any(AbortSignal));
	});

	it('renders each concept with its score, linking to the concept page', async () => {
		const items: SimilarConcept[] = [
			{ code: 'C9305', label: 'Malignant Neoplasm', score: 0.912 },
			{ code: 'C4321', label: null, score: 0.5 }
		];
		mock.mockResolvedValue(items);
		render(SimilarConcepts, { code: 'C3262' });

		const link = await screen.findByRole('link', { name: 'Malignant Neoplasm' });
		expect(link).toHaveAttribute('href', '/repositories/ncit/C9305');
		expect(screen.getByText('0.91')).toBeInTheDocument(); // score to 2 dp
		// A concept with no label falls back to its code.
		expect(screen.getByRole('link', { name: 'C4321' })).toBeInTheDocument();
	});

	it('shows the unavailable state on fetch failure', async () => {
		mock.mockRejectedValue(new Error('network error'));
		render(SimilarConcepts, { code: 'C3262' });
		expect(await screen.findByText('Embeddings unavailable.')).toBeInTheDocument();
	});
});
