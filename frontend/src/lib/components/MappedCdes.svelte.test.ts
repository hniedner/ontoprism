import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import MappedCdes from './MappedCdes.svelte';
import type { CdeSummary } from '$lib/types';

vi.mock('$lib/api', () => ({ cdesForConcept: vi.fn() }));
import { cdesForConcept } from '$lib/api';

const mock = vi.mocked(cdesForConcept);

beforeEach(() => mock.mockClear());

describe('MappedCdes', () => {
	it('requests the CDEs mapped to the concept code', async () => {
		mock.mockResolvedValue([]);
		render(MappedCdes, { code: 'C3262' });
		await screen.findByText('No CDEs map to this concept.');
		expect(mock).toHaveBeenCalledWith('C3262', 25);
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
