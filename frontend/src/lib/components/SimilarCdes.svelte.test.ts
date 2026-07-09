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
	it('requests the top-10 similar CDEs for the given public id', async () => {
		mock.mockResolvedValue([]);
		render(SimilarCdes, { publicId: '100' });
		await screen.findByText('None.');
		expect(mock).toHaveBeenCalledWith('100', 10);
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
