import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import ExternalMappingsPanel from './ExternalMappingsPanel.svelte';

vi.mock('$lib/api', () => ({ getMappings: vi.fn() }));
import { getMappings } from '$lib/api';

const mock = vi.mocked(getMappings);

describe('ExternalMappingsPanel', () => {
	it('aborts replaced requests and ignores their late success and error', async () => {
		mock.mockClear();
		const first = Promise.withResolvers<Awaited<ReturnType<typeof getMappings>>>();
		const second = Promise.withResolvers<Awaited<ReturnType<typeof getMappings>>>();
		const third = Promise.withResolvers<Awaited<ReturnType<typeof getMappings>>>();
		const signals: AbortSignal[] = [];
		for (const request of [first, second, third]) {
			mock.mockImplementationOnce((_code, _fetch, signal) => {
				signals.push(signal!);
				return request.promise;
			});
		}

		const view = render(ExternalMappingsPanel, { code: 'C1' });
		await vi.waitFor(() => expect(mock).toHaveBeenCalledTimes(1));
		await view.rerender({ code: 'C2' });
		await vi.waitFor(() => expect(mock).toHaveBeenCalledTimes(2));
		await view.rerender({ code: 'C3' });
		await vi.waitFor(() => expect(mock).toHaveBeenCalledTimes(3));
		expect(signals.slice(0, 2).every((signal) => signal.aborted)).toBe(true);

		third.resolve({
			code: 'C3',
			mappings: [{ object_id: 'NEW:3', predicate: 'exactMatch', lifecycle: 'validated', confidence: 1, is_identity: true }]
		});
		expect(await screen.findByText('NEW:3')).toBeInTheDocument();
		first.resolve({
			code: 'C1',
			mappings: [{ object_id: 'OLD:1', predicate: 'exactMatch', lifecycle: 'validated', confidence: 1, is_identity: true }]
		});
		second.reject(new Error('stale failure'));
		await Promise.allSettled([first.promise, second.promise]);
		await Promise.resolve();
		expect(screen.queryByText('OLD:1')).not.toBeInTheDocument();
		expect(screen.queryByText(/stale failure/)).not.toBeInTheDocument();
	});

	it('requests the mappings for the concept code', async () => {
		mock.mockResolvedValue({ code: 'C12400', mappings: [] });
		render(ExternalMappingsPanel, { code: 'C12400' });
		await screen.findByText('No upstream mappings.');
		expect(mock).toHaveBeenCalledWith('C12400', undefined, expect.any(AbortSignal));
	});

	it('shows an error message when the fetch fails', async () => {
		mock.mockRejectedValue(new Error('network error'));
		render(ExternalMappingsPanel, { code: 'C12400' });
		expect(await screen.findByText(/Failed to load/)).toBeInTheDocument();
	});

	it('renders mapping entries with badge and confidence', async () => {
		mock.mockResolvedValue({
			code: 'C12400',
			mappings: [
				{
					object_id: 'UBERON:0002046',
					predicate: 'http://www.w3.org/2004/02/skos/core#exactMatch',
					lifecycle: 'validated',
					confidence: 0.95,
					is_identity: true
				},
				{
					object_id: 'UBERON:0002048',
					predicate: 'http://www.w3.org/2004/02/skos/core#closeMatch',
					lifecycle: 'proposed',
					confidence: 0.7,
					is_identity: false
				}
			]
		});
		render(ExternalMappingsPanel, { code: 'C12400' });
		expect(await screen.findByText('UBERON:0002046')).toBeInTheDocument();
		expect(screen.getByText('95%')).toBeInTheDocument();
		expect(screen.getByText('core#exact')).toBeInTheDocument();
		expect(screen.getByText('identity')).toBeInTheDocument();
	});
});
