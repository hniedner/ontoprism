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
		const { container } = render(DataTableTestHost, {
			rows,
			operations: { kind: 'none' },
			controls: false
		});
		const region = screen.getByRole('region', { name: 'Loaded records table' });
		const table = within(region).getByRole('table', { name: 'Loaded records' });
		expect(table).toHaveClass('table-auto', 'min-w-full', 'border-separate');
		expect(region).toHaveClass('overflow-x-auto');
		expect(within(table).getAllByRole('columnheader')).toHaveLength(4);
		expect(container.querySelector('thead')).toBeInTheDocument();
		expect(container.querySelector('tbody')).toBeInTheDocument();
		expect(container.querySelector('th')).toHaveAttribute('scope', 'col');
		expect(container.querySelectorAll('thead tr:nth-child(2) td')).toHaveLength(0);
		expect(within(table).getAllByText('Two')[0].tagName).toBe('STRONG');
		expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
	});

	it('uses data cells for the filter row and cell borders for separated table dividers', () => {
		const { container } = render(DataTableTestHost, { rows });
		expect(container.querySelectorAll('thead tr:nth-child(2) th')).toHaveLength(0);
		expect(container.querySelectorAll('thead tr:nth-child(2) td')).toHaveLength(4);
		for (const cell of container.querySelectorAll('thead th, thead td, tbody td')) {
			expect(cell).toHaveClass('border-b');
		}
		for (const row of container.querySelectorAll('tr')) expect(row).not.toHaveClass('border-b');
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

	it('shows source-empty after rows disappear while a filter remains active', async () => {
		const { rerender } = render(DataTableTestHost, { rows });
		await fireEvent.input(screen.getByRole('searchbox', { name: 'Filter loaded names' }), {
			target: { value: 'missing' }
		});
		expect(screen.getByText('No loaded rows match the page-local filters.')).toBeInTheDocument();
		await rerender({ rows: [] });
		expect(screen.getByText('No source records.')).toBeInTheDocument();
		expect(screen.queryByText('No loaded rows match the page-local filters.')).not.toBeInTheDocument();
	});

	it('renders source-empty without component-owned loading or error modes', () => {
		const { rerender } = render(DataTableTestHost, { rows: [] });
		expect(screen.getByText('No source records.')).toBeInTheDocument();
		rerender({ rows });
		expect(bodyNames()).toEqual(['Beta', 'alpha', 'Alpha', '—']);
	});

	it.each([
		['empty columns', { rows, emptyColumns: true }],
		['blank caption', { rows, caption: '  ' }],
		['blank region label', { rows, regionLabel: '\t' }],
		['duplicate column IDs', { rows, duplicateColumns: true }],
		['duplicate row IDs', { rows: [rows[0], { ...rows[1], id: 'b' }] }],
		['empty row IDs', { rows: [{ ...rows[0], id: '' }] }],
		['invalid initial sort', { rows, initialSort: { columnId: 'missing', direction: 'asc' } }],
		['blank filter labels', { rows, invalidFilter: true }],
		['negative sticky offsets', { rows, sticky: true, invalidSticky: true }],
		['operations none with controls', { rows, operations: { kind: 'none' } }]
	])('renders a loud sanitized error and no rows for %s', (_label, props) => {
		render(DataTableTestHost, props as never);
		expect(screen.getByRole('alert')).toHaveTextContent(/DataTable/);
		expect(document.querySelector('tbody')).not.toBeInTheDocument();
		expect(screen.queryByText('Beta')).not.toBeInTheDocument();
	});

	it.each([Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY])(
		'refuses the non-finite numeric sort value %s',
		(rank) => {
			render(DataTableTestHost, { rows: [{ ...rows[0], rank }] });
			expect(screen.getByRole('alert')).toHaveTextContent(/finite numeric sort values/);
			expect(screen.queryByText('Beta')).not.toBeInTheDocument();
		}
	);

	it('refuses mixed scalar types without including source values in the error', () => {
		const mixed = [{ ...rows[0], rank: 2 }, { ...rows[1], rank: 'one' as unknown as number }];
		render(DataTableTestHost, { rows: mixed });
		expect(screen.getByRole('alert')).toHaveTextContent(
			'DataTable column "rank" returned mixed sortable scalar types'
		);
		expect(screen.getByRole('alert')).not.toHaveTextContent(/Beta|one/);
		expect(document.querySelector('tbody')).not.toBeInTheDocument();
	});

	it('revalidates changed rows, clears stale rows, and exposes no invalid source values', async () => {
		const { rerender } = render(DataTableTestHost, { rows });
		await rerender({ rows: [rows[0], { ...rows[1], id: 'b', name: 'SECRET' }] });
		expect(screen.getByRole('alert')).toHaveTextContent('DataTable duplicate row ID');
		expect(screen.getByRole('alert')).not.toHaveTextContent(/Beta|SECRET/);
		expect(document.querySelector('tbody')).not.toBeInTheDocument();
	});

	it('sanitizes failures raised by row projections even when their text mimics a contract error', () => {
		render(DataTableTestHost, {
			rows,
			rowId: () => {
				throw new Error('DataTable SECRET source value');
			}
		});
		expect(screen.getByRole('alert')).toHaveTextContent('DataTable row validation failed');
		expect(screen.getByRole('alert')).not.toHaveTextContent('SECRET');
		expect(document.querySelector('tbody')).not.toBeInTheDocument();
	});

	it('initializes sorting once and preserves the user selection across rerenders', async () => {
		const { rerender } = render(DataTableTestHost, {
			rows,
			initialSort: { columnId: 'name', direction: 'asc' }
		});
		await fireEvent.click(screen.getByRole('button', { name: 'Sort by Name' }));
		expect(bodyNames()).toEqual(['Beta', 'alpha', 'Alpha', '—']);
		await rerender({ rows: [...rows], initialSort: { columnId: 'name', direction: 'asc' } });
		expect(bodyNames()).toEqual(['Beta', 'alpha', 'Alpha', '—']);
		expect(screen.getByRole('columnheader', { name: /Name/ })).toHaveAttribute('aria-sort', 'descending');
	});
});
