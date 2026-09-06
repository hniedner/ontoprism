import { fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import { createRawSnippet } from 'svelte';
import { describe, expect, it, vi } from 'vitest';
import DataTable from './DataTable.svelte';
import DataTableTestHost, { type TestRow } from './DataTable-fixture.svelte';
import { validateDataTable } from './data-table';
import type { DataTableColumn, DataTableOperations } from './types';

const rows: readonly TestRow[] = [
	{ id: 'b', name: 'Beta', group: 'Two', rank: 2, active: true },
	{ id: 'a', name: 'alpha', group: 'One', rank: 1, active: false }
];

function bodyNames(): string[] {
	return screen.getAllByRole('row')
		.filter((row) => within(row).queryAllByRole('cell').length > 0)
		.map((row) => within(row).getAllByRole('cell')[0].textContent?.trim() ?? '');
}

describe('DataTable server-owned operations', () => {
	it('renders server order and emits sort intent without mutating rows', async () => {
		const onintent = vi.fn();
		render(DataTableTestHost, { rows, onintent, sort: { key: 'name', direction: 'asc' } });
		expect(bodyNames()).toEqual(['Beta', 'alpha']);
		const header = screen.getByRole('columnheader', { name: /Name/ });
		expect(header).toHaveAttribute('aria-sort', 'ascending');
		expect(screen.getByText('Sort: Name ascending')).toBeInTheDocument();
		await fireEvent.click(within(header).getByRole('button', { name: 'Sort by Name' }));
		expect(onintent).toHaveBeenCalledWith({ kind: 'sort', sort: { key: 'name', direction: 'desc' } });
		expect(bodyNames()).toEqual(['Beta', 'alpha']);
	});

	it('announces descending server sort and emits the ascending intent', async () => {
		const onintent = vi.fn();
		render(DataTableTestHost, { rows, onintent, sort: { key: 'name', direction: 'desc' } });
		const header = screen.getByRole('columnheader', { name: /Name/ });
		expect(header).toHaveAttribute('aria-sort', 'descending');
		await fireEvent.click(within(header).getByRole('button', { name: 'Sort by Name' }));
		expect(onintent).toHaveBeenCalledWith({ kind: 'sort', sort: { key: 'name', direction: 'asc' } });
	});

	it('resets an active one-way descending sort instead of advertising ascending', async () => {
		const onintent = vi.fn();
		const cell = createRawSnippet<[TestRow]>((getRow) => ({ render: () => `<span>${getRow().name}</span>` }));
		render(DataTable, {
			rows,
			columns: [{ id: 'name', label: 'Name', cell, sortable: ['desc'] }],
			caption: 'Records',
			regionLabel: 'Records table',
			getRowId: (row: TestRow) => row.id,
			operations: { kind: 'server', sort: { key: 'name', direction: 'desc' }, defaultSort: null, activeSortLabel: 'Name descending', filters: {}, busy: false, onintent }
		} as never);

		const header = screen.getByRole('columnheader', { name: /Name/ });
		expect(header).toHaveAttribute('aria-sort', 'descending');
		await fireEvent.click(within(header).getByRole('button', { name: 'Sort by Name' }));
		expect(onintent).toHaveBeenCalledWith({ kind: 'reset' });
	});

	it('uses supplied complete-domain options and preserves selected values', async () => {
		const onintent = vi.fn();
		render(DataTableTestHost, {
			rows,
			onintent,
			filters: { group: { kind: 'categorical', selected: ['Archived'] } }
		});
		const trigger = screen.getByRole('button', { name: 'Filter Group, 1 selected: Archived' });
		const groupHeader = screen.getByRole('columnheader', { name: /Group/ });
		expect(within(groupHeader).getByRole('button', { name: 'Sort by Group' })).not.toBe(trigger);
		expect(trigger).toHaveAttribute('aria-expanded', 'false');
		expect(trigger).toHaveAttribute('aria-haspopup', 'dialog');
		expect(trigger).toHaveClass('text-accent');
		expect(screen.queryByRole('group', { name: 'Filter groups' })).not.toBeInTheDocument();
		await fireEvent.click(trigger);
		expect(trigger).toHaveAttribute('aria-expanded', 'true');
		const dialog = screen.getByRole('dialog', { name: 'Group filter' });
		const group = within(dialog).getByRole('group', { name: 'Filter groups' });
		expect(within(group).getAllByRole('checkbox').map((node) => node.getAttribute('aria-label'))).toEqual([
			'Current', 'Archived'
		]);
		expect(within(group).getByRole('checkbox', { name: 'Archived' })).toBeChecked();
		await fireEvent.click(within(group).getByRole('checkbox', { name: 'Current' }));
		expect(onintent).toHaveBeenCalledWith({
			kind: 'filter', columnId: 'group', filter: { kind: 'categorical', selected: ['Archived', 'Current'] }
		});
		expect(bodyNames()).toEqual(['Beta', 'alpha']);
		await fireEvent.click(within(dialog).getByRole('button', { name: 'Clear selections for Group' }));
		expect(onintent).toHaveBeenLastCalledWith({ kind: 'clear-filter', columnId: 'group' });
		await fireEvent.click(screen.getByRole('button', { name: 'Clear all filters' }));
		expect(onintent).toHaveBeenLastCalledWith({ kind: 'clear-filters' });
	});

	it('keeps one popover open and restores trigger focus after Escape and outside activation', async () => {
		render(DataTableTestHost, { rows });
		const groupTrigger = screen.getByRole('button', { name: 'Filter Group' });
		const activeTrigger = screen.getByRole('button', { name: 'Filter Active' });

		await fireEvent.click(groupTrigger);
		const initialDialog = screen.getByRole('dialog', { name: 'Group filter' });
		await fireEvent.keyDown(document, { key: 'Tab' });
		await fireEvent.pointerDown(groupTrigger);
		await fireEvent.pointerDown(within(initialDialog).getByRole('checkbox', { name: 'Current' }));
		expect(initialDialog).toBeInTheDocument();
		await fireEvent.click(groupTrigger);
		expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
		await fireEvent.click(groupTrigger);
		await fireEvent.keyDown(document, { key: 'Escape' });
		await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
		expect(groupTrigger).toHaveFocus();

		await fireEvent.click(groupTrigger);
		await fireEvent.pointerDown(screen.getByRole('button', { name: 'Reset table' }));
		await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
		expect(groupTrigger).toHaveFocus();

		await fireEvent.click(groupTrigger);
		await fireEvent.pointerDown(activeTrigger);
		expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
		await fireEvent.click(activeTrigger);
		expect(screen.getAllByRole('dialog')).toHaveLength(1);
		expect(screen.getByRole('dialog', { name: 'Active filter' })).toBeInTheDocument();
		expect(groupTrigger).toHaveAttribute('aria-expanded', 'false');
		expect(activeTrigger).toHaveAttribute('aria-expanded', 'true');
	});

	it('anchors the popover within the available viewport above or below its header', async () => {
		render(DataTableTestHost, { rows });
		const trigger = screen.getByRole('button', { name: 'Filter Group' });
		const height = vi.spyOn(window, 'innerHeight', 'get').mockReturnValue(768);
		const width = vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(1024);
		const bounds = vi.spyOn(trigger, 'getBoundingClientRect').mockReturnValue({
			bottom: 728, height: 28, left: 100, right: 128, top: 700, width: 28, x: 100, y: 700,
			toJSON: () => ({})
		});

		await fireEvent.click(trigger);
		const dialog = screen.getByRole('dialog', { name: 'Group filter' });
		const anchor = dialog.parentElement;
		if (!anchor) throw new Error('filter dialog has no positioning anchor');
		const scrollHeight = vi.spyOn(anchor, 'scrollHeight', 'get').mockReturnValue(400);
		window.dispatchEvent(new Event('resize'));
		await waitFor(() => expect(anchor).toHaveStyle('bottom: 72px; left: 100px; max-height: 692px; width: 288px'));

		bounds.mockReturnValue({
			bottom: 48, height: 28, left: 100, right: 128, top: 20, width: 28, x: 100, y: 20,
			toJSON: () => ({})
		});
		window.dispatchEvent(new Event('resize'));
		await waitFor(() => expect(anchor).toHaveStyle('left: 100px; max-height: 712px; top: 52px; width: 288px'));

		scrollHeight.mockRestore();
		bounds.mockRestore();
		width.mockRestore();
		height.mockRestore();
	});

	it('fails closed for duplicate row identity and never renders source values', () => {
		render(DataTableTestHost, { rows: [rows[0], { ...rows[1], id: 'b', name: 'SECRET' }] });
		expect(screen.getByRole('alert')).toHaveTextContent('DataTable duplicate row ID');
		expect(screen.getByRole('alert')).not.toHaveTextContent(/Beta|SECRET/);
		expect(document.querySelector('tbody')).not.toBeInTheDocument();
	});

	it('renders authoritative no-match copy without inventing a page-local state', () => {
		render(DataTableTestHost, { rows: [], emptyMessage: 'No records match these server filters.' });
		expect(screen.getByText('No records match these server filters.')).toBeInTheDocument();
		expect(screen.queryByText(/loaded page|page-local/i)).not.toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Filter Group' })).toBeInTheDocument();
		expect(screen.getAllByRole('row').filter((row) => within(row).queryAllByRole('columnheader').length > 0)).toHaveLength(1);
	});

	it('renders a read-only table without exposing server controls', () => {
		const cell = createRawSnippet<[TestRow]>((getRow) => ({ render: () => `<span>${getRow().name}</span>` }));
		render(DataTable, {
			rows,
			columns: [{ id: 'name', label: 'Name', cell }],
			caption: 'Read-only records',
			regionLabel: 'Read-only records table',
			getRowId: (row: TestRow) => row.id,
			operations: { kind: 'none' }
		} as never);
		expect(screen.getByText('Beta')).toBeInTheDocument();
		expect(screen.getByText('alpha')).toBeInTheDocument();
		expect(screen.queryByRole('button', { name: 'Reset table' })).not.toBeInTheDocument();
	});
});

describe('DataTable fail-closed validation', () => {
	const cell = {} as never;
	const column = (changes: Record<string, unknown> = {}): DataTableColumn<TestRow> =>
		({ id: 'name', label: 'Name', cell, ...changes }) as DataTableColumn<TestRow>;
	const server = (changes: Record<string, unknown> = {}): DataTableOperations => ({
		kind: 'server', sort: null, defaultSort: null, activeSortLabel: 'Source order', filters: {}, busy: false, onintent: () => {}, ...changes
	}) as DataTableOperations;
	const check = ({
		rows: inputRows = rows, columns = [column()], operations = { kind: 'none' } as DataTableOperations,
		caption = 'Records', regionLabel = 'Records table', emptyMessage = 'No records.', getRowId = (row: TestRow) => row.id
	}: {
		rows?: readonly TestRow[]; columns?: readonly DataTableColumn<TestRow>[]; operations?: DataTableOperations;
		caption?: string; regionLabel?: string; emptyMessage?: string; getRowId?: (row: TestRow) => string;
	} = {}) => validateDataTable(inputRows, columns, getRowId, operations, caption, regionLabel, emptyMessage);

	it.each([
		['empty caption', { caption: ' ' }, 'DataTable caption must not be empty'],
		['empty region label', { regionLabel: '' }, 'DataTable region label must not be empty'],
		['empty state message', { emptyMessage: '' }, 'DataTable empty message must not be empty'],
		['no columns', { columns: [] }, 'DataTable columns must not be empty'],
		['empty column ID', { columns: [column({ id: '' })] }, 'DataTable column ID must not be empty'],
		['empty column label', { columns: [column({ label: '' })] }, 'DataTable column "name" label must not be empty'],
		['duplicate column ID', { columns: [column(), column()] }, 'DataTable duplicate column ID "name"'],
		['empty filter label', { columns: [column({ filter: { kind: 'categorical', ariaLabel: '', options: [] } })] }, 'DataTable column "name" filter aria label must not be empty'],
		['duplicate categorical option', { columns: [column({ filter: { kind: 'categorical', ariaLabel: 'Groups', options: [{ value: 'a', label: 'A' }, { value: 'a', label: 'Again' }] } })] }, 'DataTable column "name" has duplicate options'],
		['infinite sticky offset', { columns: [column({ sticky: { side: 'left', offset: Number.POSITIVE_INFINITY } })] }, 'DataTable column "name" has an invalid sticky offset'],
		['negative sticky offset', { columns: [column({ sticky: { side: 'left', offset: -1 } })] }, 'DataTable column "name" has an invalid sticky offset'],
		['empty active sort label', { operations: server({ activeSortLabel: '' }) }, 'DataTable active sort label must not be empty'],
		['unknown sort column', { operations: server({ sort: { key: 'missing', direction: 'asc' } }) }, 'DataTable sort key "missing" does not support asc'],
		['unconfigured filter', { operations: server({ filters: { missing: { kind: 'categorical', selected: ['x'] } } }) }, 'DataTable filter "missing" is not configured'],
		['invalid selected option', { columns: [column({ filter: { kind: 'categorical', ariaLabel: 'Groups', options: [{ value: 'a', label: 'A' }] } })], operations: server({ filters: { name: { kind: 'categorical', selected: ['b'] } } }) }, 'DataTable filter "name" selected an invalid option'],
		['disabled sortable column', { columns: [column({ sortable: ['asc', 'desc'] })] }, 'DataTable operations "none" cannot configure sorting or filtering'],
		['blank row ID', { getRowId: (): string => ' ' }, 'DataTable row IDs must not be empty'],
		['duplicate row ID', { getRowId: (): string => 'same' }, 'DataTable duplicate row ID']
	] as const)('rejects %s', (_name, input, message) => {
		expect(check(input as never)).toEqual({ valid: false, message });
	});

	it('does not expose unexpected row-identity exceptions', () => {
		expect(check({ getRowId: () => { throw new Error('secret'); } })).toEqual({
			valid: false, message: 'DataTable row validation failed'
		});
	});
});
