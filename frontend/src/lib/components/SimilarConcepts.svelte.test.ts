import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import SimilarConcepts from './SimilarConcepts.svelte';
import type { SimilarConcept } from '$lib/types';

vi.mock('$lib/api', () => ({ similarConcepts: vi.fn() }));
import { similarConcepts } from '$lib/api';

const mock = vi.mocked(similarConcepts);

describe('SimilarConcepts', () => {
	it('requests the top-10 similar concepts for the given code', async () => {
		mock.mockResolvedValue([]);
		render(SimilarConcepts, { code: 'C3262' });
		await screen.findByText('None.');
		expect(mock).toHaveBeenCalledWith('C3262', 10);
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
