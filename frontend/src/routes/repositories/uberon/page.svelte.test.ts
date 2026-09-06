import { fireEvent, render, screen } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import Page from './+page.svelte';

const goto = vi.fn().mockResolvedValue(undefined);
const { navigation } = vi.hoisted(() => ({
	navigation: { to: { url: new URL('https://example.test/repositories/uberon?q=heart') } as { url: URL } | null }
}));
vi.mock('$app/navigation', () => ({ goto: (target: string) => goto(target) }));
vi.mock('$app/paths', () => ({ resolve: (target: string) => target }));
vi.mock('$app/state', () => ({
	page: { url: new URL('https://example.test/repositories/uberon?q=lung&source=uberon&offset=25') },
	navigating: navigation
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
		size: 25,
		sort: 'relevance',
		filters: { source: ['uberon'] }
	}
};

describe('Uberon repository page table ownership', () => {
	beforeEach(() => goto.mockClear());

	it('sends sort and categorical filter intents through canonical URL state', async () => {
		render(Page, { data: data as never, params: {}, form: null });
		expect(screen.getAllByRole('button', { name: 'Previous page' })).toHaveLength(1);

		await fireEvent.click(screen.getByRole('button', { name: 'Sort by Code' }));
		expect(goto).toHaveBeenLastCalledWith('/repositories/uberon?q=lung&source=uberon&sort=code%3Aasc');

		await fireEvent.click(screen.getByRole('button', { name: 'Filter Source, 1 selected: Uberon' }));
		await fireEvent.click(screen.getByRole('checkbox', { name: 'Cell Ontology' }));
		expect(goto).toHaveBeenLastCalledWith('/repositories/uberon?q=lung&source=uberon&source=cl');

		await fireEvent.click(screen.getByRole('checkbox', { name: 'Uberon' }));
		expect(goto).toHaveBeenLastCalledWith('/repositories/uberon?q=lung');
	});

	it('marks the real repository table busy during same-route revalidation', () => {
		render(Page, { data: data as never, params: {}, form: null });

		expect(screen.getByRole('region', { name: 'Uberon and Cell Ontology repository results' })).toHaveAttribute('aria-busy', 'true');
	});

	it('keeps the server table and selected filter controls mounted for an empty page', async () => {
		render(Page, {
			data: {
				initial: {
					...data.initial,
					result: { ...data.initial.result, total: 0, offset: 0, hits: [] },
					query: '',
					offset: 0,
					filters: { source: ['uberon'] }
				}
			} as never,
			params: {},
			form: null
		});

		expect(screen.getByRole('region', { name: 'Uberon and Cell Ontology repository results' })).toBeInTheDocument();
		expect(screen.getByRole('columnheader', { name: /Source/ })).toBeInTheDocument();
		await fireEvent.click(screen.getByRole('button', { name: 'Filter Source, 1 selected: Uberon' }));
		expect(screen.getByRole('checkbox', { name: 'Uberon' })).toBeChecked();
		expect(screen.getByRole('button', { name: 'Clear source filter' })).toHaveTextContent('source: uberon');
		expect(screen.getByRole('button', { name: 'Reset table' })).toBeInTheDocument();
		expect(screen.getByText('No records matched the current query and filters.')).toBeInTheDocument();
	});
});
