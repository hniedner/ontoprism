import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import Pagination from './Pagination.svelte';

describe('Pagination', () => {
	it('shows the current window and page count', () => {
		render(Pagination, { offset: 25, limit: 25, total: 130, onPage: vi.fn() });
		expect(screen.getByText('26')).toBeInTheDocument(); // from
		expect(screen.getByText('50')).toBeInTheDocument(); // to
		expect(screen.getByText(/Page 2 of 6/)).toBeInTheDocument();
	});

	it('disables prev/first on the first page and enables next', () => {
		render(Pagination, { offset: 0, limit: 25, total: 130, onPage: vi.fn() });
		expect(screen.getByLabelText('Previous page')).toBeDisabled();
		expect(screen.getByLabelText('First page')).toBeDisabled();
		expect(screen.getByLabelText('Next page')).toBeEnabled();
	});

	it('disables next/last on the final page', () => {
		render(Pagination, { offset: 125, limit: 25, total: 130, onPage: vi.fn() });
		expect(screen.getByLabelText('Next page')).toBeDisabled();
		expect(screen.getByLabelText('Last page')).toBeDisabled();
		expect(screen.getByLabelText('Previous page')).toBeEnabled();
	});

	it('calls onPage with the next offset', async () => {
		const onPage = vi.fn();
		render(Pagination, { offset: 25, limit: 25, total: 130, onPage });
		screen.getByLabelText('Next page').click();
		expect(onPage).toHaveBeenCalledWith(50);
		screen.getByLabelText('Last page').click();
		expect(onPage).toHaveBeenCalledWith(125); // (6-1)*25
	});

	it('handles an empty result set without crashing', () => {
		render(Pagination, { offset: 0, limit: 25, total: 0, onPage: vi.fn() });
		expect(screen.getByText(/Page 1 of 1/)).toBeInTheDocument();
		expect(screen.getByLabelText('Next page')).toBeDisabled();
	});
});
