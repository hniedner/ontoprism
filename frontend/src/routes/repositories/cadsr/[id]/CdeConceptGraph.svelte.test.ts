import { fireEvent, render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';
import type { CdeDetail, Neighborhood } from '$lib/types';
import CdeConceptGraph from './CdeConceptGraph.svelte';

vi.mock('$lib/api', () => ({ getCdeNeighborhood: vi.fn() }));
import { getCdeNeighborhood } from '$lib/api';

const mock = vi.mocked(getCdeNeighborhood);

function cde(publicId: string): CdeDetail {
	return {
		public_id: publicId,
		version: '1.0',
		short_name: `CDE ${publicId}`,
		long_name: `CDE ${publicId}`,
		context: 'NCIP',
		datatype: 'CHARACTER',
		definition: null,
		workflow_status: 'RELEASED',
		registration_status: 'Standard',
		value_domain_type: 'Enumerated',
		permissible_values: [],
		concepts: [
			{ concept_code: `C${publicId}`, concept_name: 'Concept', concept_type: null, is_primary: true }
		]
	};
}

function neighborhood(center: string): Neighborhood {
	return { center, nodes: [], edges: [] };
}

describe('CdeConceptGraph', () => {
	it('aborts replaced loads and ignores their late success and error', async () => {
		mock.mockClear();
		const first = Promise.withResolvers<Neighborhood>();
		const second = Promise.withResolvers<Neighborhood>();
		const third = Promise.withResolvers<Neighborhood>();
		const signals: AbortSignal[] = [];
		for (const request of [first, second, third]) {
			mock.mockImplementationOnce((_id, _depth, _fetch, signal) => {
				signals.push(signal!);
				return request.promise;
			});
		}

		const view = render(CdeConceptGraph, { cde: cde('1') });
		await fireEvent.click(screen.getByRole('button', { name: 'Explore in graph' }));
		await vi.waitFor(() => expect(mock).toHaveBeenCalledTimes(1));
		await view.rerender({ cde: cde('2') });
		await fireEvent.click(screen.getByRole('button', { name: 'Explore in graph' }));
		await vi.waitFor(() => expect(mock).toHaveBeenCalledTimes(2));
		await view.rerender({ cde: cde('3') });
		await fireEvent.click(screen.getByRole('button', { name: 'Explore in graph' }));
		await vi.waitFor(() => expect(mock).toHaveBeenCalledTimes(3));
		expect(signals.slice(0, 2).every((signal) => signal.aborted)).toBe(true);

		third.reject(new Error('newest failure'));
		expect(await screen.findByRole('alert')).toHaveTextContent('newest failure');
		first.resolve(neighborhood('C1'));
		second.reject(new Error('stale failure'));
		await Promise.allSettled([first.promise, second.promise]);
		await Promise.resolve();
		expect(screen.getByRole('alert')).toHaveTextContent('newest failure');
	});
});
