import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import SimilarCdes from './SimilarCdes.svelte';
import type { SimilarCde } from '$lib/types';

vi.mock('$lib/api', () => ({ similarCdes: vi.fn() }));
import { similarCdes } from '$lib/api';

const mock = vi.mocked(similarCdes);

const items: SimilarCde[] = [
	{
		public_id: '200',
		version: '1.0',
		short_name: 'AGE',
		long_name: 'Patient Age',
		context: 'caDSR',
		datatype: 'NUMBER',
		score: 0.88
	}
];

describe('SimilarCdes', () => {
	it('aborts replaced requests and ignores their late success and error', async () => {
		mock.mockClear();
		const first = Promise.withResolvers<SimilarCde[]>();
		const second = Promise.withResolvers<SimilarCde[]>();
		const third = Promise.withResolvers<SimilarCde[]>();
		const signals: AbortSignal[] = [];
		for (const request of [first, second, third]) {
			mock.mockImplementationOnce((_id, _limit, _fetch, signal) => {
				signals.push(signal!);
				return request.promise;
			});
		}

		const view = render(SimilarCdes, { publicId: '1' });
		await vi.waitFor(() => expect(mock).toHaveBeenCalledTimes(1));
		await view.rerender({ publicId: '2' });
		await vi.waitFor(() => expect(mock).toHaveBeenCalledTimes(2));
		await view.rerender({ publicId: '3' });
		await vi.waitFor(() => expect(mock).toHaveBeenCalledTimes(3));
		expect(signals.slice(0, 2).every((signal) => signal.aborted)).toBe(true);

		third.resolve([{ ...items[0], public_id: '300', long_name: 'Newest CDE' }]);
		expect(await screen.findByRole('link', { name: 'Newest CDE' })).toBeInTheDocument();
		first.resolve([{ ...items[0], public_id: '100', long_name: 'Stale CDE' }]);
		second.reject(new Error('stale failure'));
		await Promise.allSettled([first.promise, second.promise]);
		await Promise.resolve();
		expect(screen.queryByText('Stale CDE')).not.toBeInTheDocument();
		expect(screen.queryByText('Embeddings unavailable.')).not.toBeInTheDocument();
	});

	it('requests the top-10 similar CDEs for the given public id', async () => {
		mock.mockResolvedValue([]);
		render(SimilarCdes, { publicId: '100' });
		await screen.findByText('None.');
		expect(mock).toHaveBeenCalledWith('100', 10, undefined, expect.any(AbortSignal));
	});

	it('renders each CDE with its score, linking to the CDE page', async () => {
		mock.mockResolvedValue(items);
		render(SimilarCdes, { publicId: '100' });
		const link = await screen.findByRole('link', { name: 'Patient Age' });
		expect(link).toHaveAttribute('href', '/repositories/cadsr/200');
		expect(screen.getByText('0.88')).toBeInTheDocument();
	});

	it('shows the unavailable state on fetch failure', async () => {
		mock.mockRejectedValue(new Error('network error'));
		render(SimilarCdes, { publicId: '100' });
		expect(await screen.findByText('Embeddings unavailable.')).toBeInTheDocument();
	});
});
