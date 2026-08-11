import { fireEvent, render, screen } from '@testing-library/svelte';
import { createRawSnippet } from 'svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import RepoBrowsePage from './RepoBrowsePage.svelte';

const goto = vi.fn().mockResolvedValue(undefined);
vi.mock('$app/navigation', () => ({ goto: (target: string) => goto(target) }));
vi.mock('$app/paths', () => ({ resolve: (target: string) => target }));

interface Hit {
	id: string;
}

const helpText = createRawSnippet(() => ({
	render: () => `<span data-testid="help">help copy</span>`
}));
const results = createRawSnippet<[Hit[]]>((getHits) => ({
	render: () => `<div data-testid="results">${getHits().length} rows</div>`
}));

function setup(query = '', offset = 0, total = 42) {
	return render(RepoBrowsePage, {
		title: 'NCIt Browser',
		description: 'Browse concepts',
		route: '/repositories/ncit',
		helpText,
		placeholder: 'Search…',
		ariaLabel: 'Search NCIt',
		suggestions: ['melanoma'],
		browseTitle: 'All concepts',
		countLabel: (count: number, mode: string) => `${count} (${mode})`,
		results: results as never,
		initial: { result: { total, hits: [{ id: 'a' }] }, query, offset }
	});
}

describe('RepoBrowsePage', () => {
	beforeEach(() => goto.mockClear());

	it('renders server-loaded browse data and a progressively functional GET form', () => {
		setup();

		expect(screen.getByRole('heading', { name: 'NCIt Browser' })).toBeInTheDocument();
		expect(screen.getByText('All concepts')).toBeInTheDocument();
		expect(screen.getByText('42 (browse)')).toBeInTheDocument();
		expect(screen.getByTestId('results')).toHaveTextContent('1 rows');
		const searchbox = screen.getByRole('searchbox');
		expect(searchbox).toHaveAttribute('name', 'q');
		expect(searchbox.closest('form')).toHaveAttribute('method', 'get');
	});

	it('renders server-loaded search and pagination state', () => {
		setup('melanoma', 25, 100);

		expect(screen.getByRole('searchbox')).toHaveValue('melanoma');
		expect(screen.getByText('Results for “melanoma”')).toBeInTheDocument();
		expect(screen.getByText('100 (search)')).toBeInTheDocument();
		expect(screen.getByText('Page 2 of 4')).toBeInTheDocument();
	});

	it('enhances search and pagination as URL navigation', async () => {
		setup('', 0, 100);
		await fireEvent.input(screen.getByRole('searchbox'), { target: { value: 'melanoma' } });
		await fireEvent.click(screen.getByRole('button', { name: 'Search' }));
		expect(goto).toHaveBeenLastCalledWith('/repositories/ncit?q=melanoma');

		await fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
		expect(goto).toHaveBeenLastCalledWith('/repositories/ncit?offset=25');
	});

	it('uses suggestion chips to update the URL query', async () => {
		setup();
		await fireEvent.click(screen.getByRole('button', { name: 'melanoma' }));
		expect(goto).toHaveBeenCalledWith('/repositories/ncit?q=melanoma');
	});
});
