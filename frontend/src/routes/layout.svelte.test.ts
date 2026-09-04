import { render, screen } from '@testing-library/svelte';
import { createRawSnippet } from 'svelte';
import { describe, expect, it, vi } from 'vitest';

vi.mock('$app/state', () => ({
	navigating: { to: null },
	page: { url: new URL('http://localhost/') }
}));
vi.mock('$lib/stores/theme.svelte', () => ({
	theme: { current: 'dark', toggle: vi.fn() }
}));

import Layout from './+layout.svelte';

const children = createRawSnippet(() => ({ render: () => '<p>Route content</p>' }));

describe('application layout', () => {
	it('labels the application as the current NCIt-centered product without planned claims', () => {
		render(Layout, { data: { icdoAccess: 'unavailable' }, children, params: {} });

		expect(screen.getByText('Current product · NCIt-centered ontology exploration')).toBeVisible();
		expect(screen.getByText('Route content')).toBeVisible();
		expect(document.body).not.toHaveTextContent(
			/generic adapter|ontology editing|generic reasoning|AI authoring|release reconciliation/i
		);
	});
});
