import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import MappedCdes from './MappedCdes.svelte';
import type { CdeSummary } from '$lib/types';

vi.mock('$lib/api', () => ({ cdesForConcept: vi.fn() }));
import { cdesForConcept } from '$lib/api';

const mock = vi.mocked(cdesForConcept);

describe('MappedCdes', () => {
	it('aborts replaced requests and ignores their late success and error', async () => {
		mock.mockClear();
		const first = Promise.withResolvers<CdeSummary[]>();
		const second = Promise.withResolvers<CdeSummary[]>();
		const third = Promise.withResolvers<CdeSummary[]>();
		const signals: AbortSignal[] = [];
		for (const request of [first, second, third]) {
			mock.mockImplementationOnce((_code, _limit, _fetch, signal) => {
				signals.push(signal!);
				return request.promise;
			});
		}
		const cde = (publicId: string, longName: string): CdeSummary => ({
			public_id: publicId,
			version: '1.0',
			short_name: longName,
			long_name: longName,
			context: 'caDSR',
			datatype: 'CHARACTER'
		});

		const view = render(MappedCdes, { code: 'C1' });
		await vi.waitFor(() => expect(mock).toHaveBeenCalledTimes(1));
		await view.rerender({ code: 'C2' });
		await vi.waitFor(() => expect(mock).toHaveBeenCalledTimes(2));
		await view.rerender({ code: 'C3' });
		await vi.waitFor(() => expect(mock).toHaveBeenCalledTimes(3));
		expect(signals.slice(0, 2).every((signal) => signal.aborted)).toBe(true);

		third.resolve([cde('300', 'Newest mapping')]);
		expect(await screen.findByRole('link', { name: 'Newest mapping' })).toBeInTheDocument();
		first.resolve([cde('100', 'Stale mapping')]);
		second.reject(new Error('stale failure'));
		await Promise.allSettled([first.promise, second.promise]);
		await Promise.resolve();
		expect(screen.queryByText('Stale mapping')).not.toBeInTheDocument();
		expect(screen.queryByRole('alert')).not.toBeInTheDocument();
	});

	it('requests the CDEs mapped to the concept code', async () => {
		mock.mockResolvedValue([]);
		render(MappedCdes, { code: 'C3262' });
		await screen.findByText('No CDEs map to this concept.');
		expect(mock).toHaveBeenCalledWith('C3262', 25, undefined, expect.any(AbortSignal));
	});

	it('shows the fallback when the fetch fails', async () => {
		mock.mockRejectedValue(new Error('network error'));
		render(MappedCdes, { code: 'C3262' });
		expect(await screen.findByRole('alert')).toHaveTextContent('Mapped CDEs are unavailable');
		expect(screen.queryByText('No CDEs map to this concept.')).not.toBeInTheDocument();
	});

	it('lists mapped CDEs linking to their detail page', async () => {
		const cdes: CdeSummary[] = [
			{
				public_id: '100',
				version: '2.0',
				short_name: 'NEO',
				long_name: 'Neoplasm Histology',
				context: 'caDSR',
				datatype: 'CHARACTER'
			}
		];
		mock.mockResolvedValue(cdes);
		render(MappedCdes, { code: 'C3262' });
		const link = await screen.findByRole('link', { name: 'Neoplasm Histology' });
		expect(link).toHaveAttribute('href', '/repositories/cadsr/100');
		expect(screen.getByText('100')).toBeInTheDocument();
	});
});
