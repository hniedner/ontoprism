import { render, screen } from '@testing-library/svelte';
import { createRawSnippet } from 'svelte';
import { describe, expect, it } from 'vitest';

import RemoteSearchSurface from './RemoteSearchSurface.svelte';

const instruction = createRawSnippet(() => ({ render: () => '<p>Enter a query</p>' }));
const children = createRawSnippet(() => ({ render: () => '<p>Search results</p>' }));

describe('RemoteSearchSurface', () => {
	it('keeps instruction, ready, and typed error states distinct', () => {
		const instructionView = render(RemoteSearchSurface, {
			service: 'PubMed',
			error: null,
			ready: false,
			instruction,
			children
		});
		expect(screen.getByText('Enter a query')).toBeVisible();
		instructionView.unmount();

		const readyView = render(RemoteSearchSurface, {
			service: 'PubMed',
			error: null,
			ready: true,
			instruction,
			children
		});
		expect(screen.getByText('Search results')).toBeVisible();
		readyView.unmount();

		render(RemoteSearchSurface, {
			service: 'PubMed',
			error: { remoteState: 'timeout', message: 'PubMed request timed out.' },
			ready: false,
			instruction,
			children
		});
		expect(screen.getByRole('alert')).toHaveAttribute('data-remote-state', 'timeout');
	});
});
