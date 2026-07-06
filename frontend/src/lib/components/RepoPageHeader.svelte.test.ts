import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import { tick } from 'svelte';
import { createRawSnippet } from 'svelte';
import RepoPageHeader from './RepoPageHeader.svelte';

const help = createRawSnippet(() => ({
	render: () => `<p data-testid="help-body">how to use this</p>`
}));

describe('RepoPageHeader', () => {
	it('renders the title and description', () => {
		render(RepoPageHeader, { title: 'NCIt Browser', description: 'Browse concepts' });
		expect(screen.getByRole('heading', { name: 'NCIt Browser' })).toBeInTheDocument();
		expect(screen.getByText('Browse concepts')).toBeInTheDocument();
	});

	it('shows a formatted total chip only when a total is provided', () => {
		const { unmount } = render(RepoPageHeader, {
			title: 'T',
			description: 'D',
			total: 204321
		});
		expect(screen.getByText('204,321 total')).toBeInTheDocument();
		unmount();

		render(RepoPageHeader, { title: 'T', description: 'D', total: null });
		expect(screen.queryByText(/total/)).not.toBeInTheDocument();
	});

	it('has no Help button when no help snippet is given', () => {
		render(RepoPageHeader, { title: 'T', description: 'D' });
		expect(screen.queryByRole('button', { name: /Help/ })).not.toBeInTheDocument();
	});

	it('toggles the help panel when the Help button is clicked', async () => {
		render(RepoPageHeader, { title: 'T', description: 'D', help });
		expect(screen.queryByTestId('help-body')).not.toBeInTheDocument();
		screen.getByRole('button', { name: /Help/ }).click();
		await tick();
		expect(screen.getByTestId('help-body')).toBeInTheDocument();
	});
});
