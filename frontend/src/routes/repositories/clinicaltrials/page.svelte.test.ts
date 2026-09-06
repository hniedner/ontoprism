import { fireEvent, render, screen } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import Page from './+page.svelte';

const goto = vi.fn().mockResolvedValue(undefined);
vi.mock('$app/navigation', () => ({ goto: (target: string) => goto(target) }));
vi.mock('$app/paths', () => ({ resolve: (target: string) => target }));
vi.mock('$app/state', () => ({
	page: { url: new URL('https://example.test/repositories/clinicaltrials?q=melanoma&cursor=opaque&status=RECRUITING') },
	navigating: { to: null }
}));

const data = {
	query: 'melanoma',
	size: 25,
	cursors: ['opaque'],
	filters: { status: ['RECRUITING'], phase: [] },
	result: {
		state: 'ready',
		data: {
			condition: 'melanoma', intervention: null, term: null, total: 42,
			page_size: 25, page_token: 'opaque', next_page_token: null,
			status: ['RECRUITING'], phase: [], studies: []
		}
	}
};

describe('ClinicalTrials.gov repository empty cursor page', () => {
	beforeEach(() => goto.mockClear());

	it('keeps filter identity, table controls, and previous cursor recovery mounted', async () => {
		render(Page, { data: data as never, params: {}, form: null });

		expect(screen.getByRole('region', { name: 'ClinicalTrials.gov repository results' })).toBeInTheDocument();
		for (const name of ['NCT ID', 'Title', 'Status', 'Phase']) {
			expect(screen.getByRole('columnheader', { name: new RegExp(`^${name}`) })).toBeInTheDocument();
		}
		await fireEvent.click(screen.getByRole('button', { name: 'Filter Status, 1 selected: RECRUITING' }));
		expect(screen.getByRole('checkbox', { name: 'RECRUITING' })).toBeChecked();
		expect(screen.getByRole('button', { name: 'Clear Status filter' })).toHaveTextContent('Status: RECRUITING');
		expect(screen.getByText('No trials matched “melanoma”.')).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Previous page' })).toBeEnabled();

		await fireEvent.click(screen.getByRole('checkbox', { name: 'COMPLETED' }));
		expect(goto).toHaveBeenLastCalledWith('/repositories/clinicaltrials?q=melanoma&status=RECRUITING&status=COMPLETED');
		await fireEvent.click(screen.getByRole('button', { name: 'Reset table' }));
		expect(goto).toHaveBeenLastCalledWith('/repositories/clinicaltrials?q=melanoma');
	});
});
