import { fireEvent, render, screen } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import Page from './+page.svelte';

const goto = vi.fn().mockResolvedValue(undefined);
vi.mock('$app/navigation', () => ({ goto: (target: string) => goto(target) }));
vi.mock('$app/paths', () => ({ resolve: (target: string) => target }));
vi.mock('$app/state', () => ({
	page: { url: new URL('https://example.test/repositories/uberon?q=lung&source=uberon&offset=25') },
	navigating: { to: null }
}));

const data = {
	initial: {
		result: {
			query: 'lung',
			total: 26,
			limit: 25,
			offset: 25,
			hits: [{ code: 'UBERON:0002048', label: 'lung', source: 'uberon', matched_synonym: null }]
		},
		query: 'lung',
		offset: 25,
		source: 'uberon'
	}
};

describe('Uberon repository page table ownership', () => {
	beforeEach(() => goto.mockClear());

	it('keeps source and server pagination in URL state while table controls stay local', async () => {
		render(Page, { data: data as never, params: {}, form: null });
		expect(screen.getAllByRole('button', { name: 'Previous page' })).toHaveLength(1);

		await fireEvent.click(screen.getByRole('button', { name: 'Sort by Code' }));
		await fireEvent.input(screen.getByRole('searchbox', { name: 'Filter loaded Uberon/CL names' }), {
			target: { value: 'lung' }
		});
		expect(goto).not.toHaveBeenCalled();

		await fireEvent.change(screen.getByRole('combobox', { name: 'Source' }), {
			target: { value: 'cl' }
		});
		expect(goto).toHaveBeenLastCalledWith('/repositories/uberon?q=lung&source=cl');

		await fireEvent.change(screen.getByRole('combobox', { name: 'Source' }), {
			target: { value: '' }
		});
		expect(goto).toHaveBeenLastCalledWith('/repositories/uberon?q=lung');
	});
});
