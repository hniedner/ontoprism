import { fireEvent, render, screen, within } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import DataTableTestHost, { type TestRow } from './DataTable-fixture.svelte';

const rows: readonly TestRow[] = [
	{ id: 'b', name: 'Beta', group: 'Two', rank: 2, active: true },
	{ id: 'a', name: 'alpha', group: 'One', rank: 1, active: false },
	{ id: 'a2', name: 'Alpha', group: 'One', rank: 1, active: true },
	{ id: 'n', name: null, group: 'Two', rank: null, active: false }
];

function bodyNames(): string[] {
	return screen
		.getAllByRole('row')
		.slice(2)
		.map((row) => within(row).getAllByRole('cell')[0].textContent?.trim() ?? '');
}

describe('DataTable', () => {
	it('renders an accessible responsive semantic table and compiled cell snippets', () => {
		const { container } = render(DataTableTestHost, { rows, operations: { kind: 'none' } });
		const region = screen.getByRole('region', { name: 'Loaded records table' });
		const table = within(region).getByRole('table', { name: 'Loaded records' });
		expect(table).toHaveClass('table-auto', 'min-w-full', 'border-separate');
		expect(region).toHaveClass('overflow-x-auto');
		expect(within(table).getAllByRole('columnheader')).toHaveLength(4);
		expect(container.querySelector('thead')).toBeInTheDocument();
		expect(container.querySelector('tbody')).toBeInTheDocument();
		expect(container.querySelector('th')).toHaveAttribute('scope', 'col');
		expect(within(table).getAllByText('Two')[0].tagName).toBe('STRONG');
		expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
	});

	it('sorts stably with deterministic strings and nulls last in both directions', async () => {
		render(DataTableTestHost, {
			rows,
			initialSort: { columnId: 'name', direction: 'asc' }
		});
		expect(bodyNames()).toEqual(['alpha', 'Alpha', 'Beta', '—']);
		const nameHeader = screen.getByRole('columnheader', { name: /Name/ });
		expect(nameHeader).toHaveAttribute('aria-sort', 'ascending');
		await fireEvent.click(within(nameHeader).getByRole('button', { name: 'Sort by Name' }));
		expect(bodyNames()).toEqual(['Beta', 'alpha', 'Alpha', '—']);
		expect(nameHeader).toHaveAttribute('aria-sort', 'descending');
	});

	it('AND-combines trimmed case-insensitive page-local filters and can clear them', async () => {
		render(DataTableTestHost, { rows });
		expect(screen.getByText('Filters and sorting apply to this loaded page.')).toBeVisible();
		await fireEvent.input(screen.getByRole('searchbox', { name: 'Filter loaded names' }), {
			target: { value: ' ALP ' }
		});
		await fireEvent.input(screen.getByRole('searchbox', { name: 'Filter loaded groups' }), {
			target: { value: ' one ' }
		});
		expect(bodyNames()).toEqual(['alpha', 'Alpha']);
		await fireEvent.input(screen.getByRole('searchbox', { name: 'Filter loaded groups' }), {
			target: { value: 'two' }
		});
		expect(screen.getByText('No loaded rows match the page-local filters.')).toBeInTheDocument();
		expect(screen.queryByText('No source records.')).not.toBeInTheDocument();
		await fireEvent.click(screen.getByRole('button', { name: 'Clear page-local filters' }));
		expect(bodyNames()).toEqual(['Beta', 'alpha', 'Alpha', '—']);
	});

	it('distinguishes source-empty and applies loading then error precedence', () => {
		const { rerender } = render(DataTableTestHost, { rows: [] });
		expect(screen.getByText('No source records.')).toBeInTheDocument();
		rerender({ rows, state: { kind: 'loading', label: 'Loading loaded records' } });
		expect(screen.getByText('Loading loaded records')).toBeInTheDocument();
		expect(screen.queryByRole('row')).not.toBeInTheDocument();
		rerender({ rows, state: { kind: 'error', message: 'Records unavailable' } });
		expect(screen.getByRole('alert')).toHaveTextContent('Records unavailable');
		expect(screen.queryByRole('row')).not.toBeInTheDocument();
	});

	it.each([
		['duplicate column IDs', { rows, duplicateColumns: true }],
		['duplicate row IDs', { rows: [rows[0], { ...rows[1], id: 'b' }] }],
		['empty row IDs', { rows: [{ ...rows[0], id: '' }] }],
		['invalid initial sort', { rows, initialSort: { columnId: 'missing', direction: 'asc' } }],
		['filter labels without filter values', { rows, invalidFilter: true }],
		['negative sticky offsets', { rows, sticky: true, invalidSticky: true }]
	])('fails loudly before rendering rows for %s', (_label, props) => {
		expect(() => render(DataTableTestHost, props as never)).toThrow(/DataTable/);
	});

	it('refuses mixed scalar types without including source values in the error', () => {
		const mixed = [{ ...rows[0], rank: 2 }, { ...rows[1], rank: 'one' as unknown as number }];
		expect(() => render(DataTableTestHost, { rows: mixed })).toThrow(
			/DataTable column "rank" returned mixed sortable scalar types/
		);
		expect(() => render(DataTableTestHost, { rows: mixed })).not.toThrow(/Beta|one/);
	});
});
