import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import { tick } from 'svelte';
import RepoSearchBar from './RepoSearchBar.svelte';

function setup(overrides: Record<string, unknown> = {}) {
	const onsearch = vi.fn();
	const onsuggestion = vi.fn();
	render(RepoSearchBar, {
		value: '',
		placeholder: 'Search concepts…',
		ariaLabel: 'Search NCIt',
		suggestions: ['melanoma', 'gene'],
		loading: false,
		onsearch,
		onsuggestion,
		...overrides
	});
	return { onsearch, onsuggestion };
}

describe('RepoSearchBar', () => {
	it('renders the labelled input and suggestion chips', () => {
		setup();
		expect(screen.getByRole('searchbox', { name: 'Search NCIt' })).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'melanoma' })).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'gene' })).toBeInTheDocument();
	});

	it('fires onsearch when the form is submitted', async () => {
		const { onsearch } = setup();
		screen.getByRole('button', { name: 'Search' }).click();
		await tick();
		expect(onsearch).toHaveBeenCalledOnce();
	});

	it('fires onsuggestion with the chosen term', async () => {
		const { onsuggestion } = setup();
		screen.getByRole('button', { name: 'melanoma' }).click();
		await tick();
		expect(onsuggestion).toHaveBeenCalledWith('melanoma');
	});

	it('disables the button and shows Searching… while loading', () => {
		setup({ loading: true });
		const button = screen.getByRole('button', { name: 'Searching…' });
		expect(button).toBeDisabled();
	});
});
