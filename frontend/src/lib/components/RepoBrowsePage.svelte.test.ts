import { describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/svelte';
import { createRawSnippet } from 'svelte';
import RepoBrowsePage from './RepoBrowsePage.svelte';

interface Hit {
	id: string;
}
interface Page {
	total: number;
	hits: Hit[];
}

const helpText = createRawSnippet(() => ({
	render: () => `<span data-testid="help">help copy</span>`
}));
const results = createRawSnippet<[Hit[]]>(() => ({
	render: () => `<div data-testid="results">rows</div>`
}));

function setup(searchFn: unknown, listFn: unknown) {
	return render(RepoBrowsePage, {
		title: 'NCIt Browser',
		description: 'Browse concepts',
		helpText,
		searchFn: searchFn as never,
		listFn: listFn as never,
		placeholder: 'Search…',
		ariaLabel: 'Search NCIt',
		suggestions: ['melanoma'],
		browseTitle: 'All concepts',
		countLabel: (total: number, mode: string) => `${total} (${mode})`,
		results: results as never
	});
}

describe('RepoBrowsePage', () => {
	it('loads browse mode on mount and renders the results card + header', async () => {
		const listFn = vi.fn().mockResolvedValue({ total: 42, hits: [{ id: 'a' }] } satisfies Page);
		const searchFn = vi.fn();
		setup(searchFn, listFn);

		expect(await screen.findByText('All concepts')).toBeInTheDocument();
		expect(screen.getByRole('heading', { name: 'NCIt Browser' })).toBeInTheDocument();
		expect(screen.getByTestId('results')).toBeInTheDocument();
		// countLabel is called with the total and the browse mode.
		expect(screen.getByText('42 (browse)')).toBeInTheDocument();
		expect(listFn).toHaveBeenCalledWith({ limit: 25, offset: 0 });
		expect(searchFn).not.toHaveBeenCalled();
	});

	it('shows nothing before the initial load completes', async () => {
		const { promise, resolve } = Promise.withResolvers();
		const listFn = vi.fn().mockReturnValue(promise);
		setup(vi.fn(), listFn);
		// Before the promise settles, no results card or pagination is shown.
		expect(screen.queryByText('All concepts')).not.toBeInTheDocument();
		expect(screen.queryByTestId('results')).not.toBeInTheDocument();
		resolve({ total: 42, hits: [{ id: 'a' }] } satisfies Page);
		await screen.findByText('All concepts');
	});

	it('runs a search and switches the results title to the query', async () => {
		const listFn = vi.fn().mockResolvedValue({ total: 42, hits: [] } satisfies Page);
		const searchFn = vi.fn().mockResolvedValue({ total: 1, hits: [{ id: 'x' }] } satisfies Page);
		setup(searchFn, listFn);
		await screen.findByText('All concepts');

		await fireEvent.input(screen.getByRole('searchbox'), { target: { value: 'melanoma' } });
		await fireEvent.click(screen.getByRole('button', { name: 'Search' }));

		expect(await screen.findByText('Results for “melanoma”')).toBeInTheDocument();
		expect(searchFn).toHaveBeenCalledWith('melanoma', { limit: 25, offset: 0 });
		expect(screen.getByText('1 (search)')).toBeInTheDocument();
	});

	it('shows the error banner when the initial load fails', async () => {
		const listFn = vi.fn().mockRejectedValue(new Error('backend down'));
		setup(vi.fn(), listFn);
		expect(await screen.findByText(/Search failed: backend down/)).toBeInTheDocument();
		// No results table rendered on the error path.
		expect(screen.queryByTestId('results')).not.toBeInTheDocument();
	});

	it('falls back to empty hits and zero total when the API omits them', async () => {
		const listFn = vi.fn().mockResolvedValue({ hits: null, total: null } as unknown as Page);
		setup(vi.fn(), listFn);
		// countLabel receives raw null (the ?? 0 is in <Pagination>, not the label),
		// but the results snippet should still render (empty array fallback).
		expect(await screen.findByTestId('results')).toBeInTheDocument();
	});

	it('loads a search when a suggestion chip is clicked', async () => {
		const listFn = vi.fn().mockResolvedValue({ total: 42, hits: [{ id: 'a' }] } satisfies Page);
		const searchFn = vi.fn().mockResolvedValue({ total: 1, hits: [{ id: 'x' }] } satisfies Page);
		setup(searchFn, listFn);
		await screen.findByText('All concepts');

		await fireEvent.click(screen.getByRole('button', { name: 'melanoma' }));
		await screen.findByText(/Results for .*melanoma/);
		expect(searchFn).toHaveBeenCalledWith('melanoma', { limit: 25, offset: 0 });
	});

	it('loads the next page when a pagination control is clicked', async () => {
		const listFn = vi.fn().mockResolvedValue({ total: 100, hits: [{ id: 'a' }, { id: 'b' }] } satisfies Page);
		const searchFn = vi.fn();
		setup(searchFn, listFn);
		await screen.findByText('All concepts');
		expect(listFn).toHaveBeenCalledTimes(1);

		await fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
		await screen.findByText('Page 2 of 4');
		expect(listFn).toHaveBeenCalledTimes(2);
		expect(listFn).toHaveBeenLastCalledWith({ limit: 25, offset: 25 });
	});
});
