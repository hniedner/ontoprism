import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import { createRawSnippet } from 'svelte';
import RepoResultsCard from './RepoResultsCard.svelte';

const children = createRawSnippet(() => ({
	render: () => `<div data-testid="table">rows</div>`
}));

describe('RepoResultsCard', () => {
	it('renders the error banner instead of the card when there is an error', () => {
		render(RepoResultsCard, {
			title: 'Results',
			countLabel: '3 hits',
			loading: false,
			error: 'boom',
			children
		});
		expect(screen.getByText(/Search failed: boom/)).toBeInTheDocument();
		expect(screen.queryByTestId('table')).not.toBeInTheDocument();
	});

	it('shows a loading message instead of the children while loading', () => {
		render(RepoResultsCard, {
			title: 'Results',
			countLabel: '',
			loading: true,
			error: null,
			children
		});
		expect(screen.getByText('Loading…')).toBeInTheDocument();
		expect(screen.queryByTestId('table')).not.toBeInTheDocument();
	});

	it('renders the title, count and children when loaded', () => {
		render(RepoResultsCard, {
			title: 'Results for melanoma',
			countLabel: '42 results',
			loading: false,
			error: null,
			children
		});
		expect(screen.getByText('Results for melanoma')).toBeInTheDocument();
		expect(screen.getByText('42 results')).toBeInTheDocument();
		expect(screen.getByTestId('table')).toBeInTheDocument();
	});
});
