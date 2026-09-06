import { fireEvent, render, screen } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import Page from './+page.svelte';

const goto = vi.fn().mockResolvedValue(undefined);
vi.mock('$app/navigation', () => ({ goto: (target: string) => goto(target) }));
vi.mock('$app/paths', () => ({ resolve: (target: string) => target }));
vi.mock('$app/state', () => ({
	page: { url: new URL('https://example.test/repositories/pubmed?q=missing&size=50&offset=50&sort=pub_date') },
	navigating: { to: null }
}));

const data = {
	query: 'missing',
	offset: 50,
	size: 50,
	sort: 'pub_date',
	result: {
		state: 'ready',
		data: { query: 'missing', total: 0, limit: 25, offset: 0, sort: 'pub_date', articles: [] }
	}
};

describe('PubMed repository empty page', () => {
	beforeEach(() => goto.mockClear());

	it('keeps the sorted server table and reset control mounted for no matches', async () => {
		render(Page, { data: data as never, params: {}, form: null });

		expect(screen.getByRole('region', { name: 'PubMed repository results' })).toBeInTheDocument();
		expect(screen.getByRole('columnheader', { name: /Date/ })).toHaveAttribute('aria-sort', 'descending');
		expect(screen.getByText('No articles matched “missing”.')).toBeInTheDocument();
		await fireEvent.click(screen.getByRole('button', { name: 'Reset table' }));
		expect(goto).toHaveBeenLastCalledWith('/repositories/pubmed?q=missing');
	});

	it('maps the publication Date column to the exact upstream date sort', async () => {
		render(Page, { data: { ...data, sort: 'relevance' } as never, params: {}, form: null });

		await fireEvent.click(screen.getByRole('button', { name: 'Sort by Date' }));
		expect(goto).toHaveBeenLastCalledWith('/repositories/pubmed?q=missing&size=50&sort=pub_date');
	});
});
