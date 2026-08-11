import { render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';
import BrowserGraph from './BrowserGraph.svelte';

describe('BrowserGraph', () => {
	it('reserves the requested height while the browser graph loads', () => {
		const loader = vi.fn(() => new Promise<never>(() => undefined));
		const { container } = render(BrowserGraph, { code: 'C3262', height: '24rem', loader });

		expect(screen.getByRole('status')).toHaveTextContent('Loading concept graph');
		expect(container.firstElementChild).toHaveStyle({ minHeight: '24rem' });
	});

	it('turns a dynamic-import failure into an explicit stable error region', async () => {
		const loader = vi.fn().mockRejectedValue(new Error('chunk unavailable'));
		render(BrowserGraph, { code: 'C3262', height: '24rem', loader });

		expect(await screen.findByRole('alert')).toHaveTextContent('Concept graph is unavailable');
		expect(screen.getByRole('alert')).toHaveStyle({ minHeight: '24rem' });
	});
});
