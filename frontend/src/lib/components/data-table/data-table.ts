import type {
	DataTableColumn,
	DataTableInitialSort,
	DataTableOperations,
	DataTableScalar,
	DataTableSortDirection
} from './types';

function scalarType(value: Exclude<DataTableScalar, null>): string {
	return typeof value;
}

function assertText(name: string, value: string): void {
	if (!value.trim()) throw new Error(`DataTable ${name} must not be empty`);
}

function validateColumn<Row>(column: DataTableColumn<Row>): void {
		assertText('column ID', column.id);
		assertText(`column "${column.id}" label`, column.label);
		if (column.filterAriaLabel !== undefined && column.filterValue === undefined) {
			throw new Error(`DataTable column "${column.id}" has a filter label without a filter value`);
		}
		if (column.filterValue !== undefined) {
			if (column.filterAriaLabel === undefined) {
				throw new Error(`DataTable column "${column.id}" requires a filter aria label`);
			}
			assertText(`column "${column.id}" filter aria label`, column.filterAriaLabel);
			if (column.filterPlaceholder !== undefined) {
				assertText(`column "${column.id}" filter placeholder`, column.filterPlaceholder);
			}
		}
		if (
			column.sticky !== undefined &&
			(!Number.isFinite(column.sticky.offset) || column.sticky.offset < 0)
		) {
			throw new Error(`DataTable column "${column.id}" has an invalid sticky offset`);
		}
	}

function validateColumns<Row>(columns: readonly DataTableColumn<Row>[]): void {
	const columnIds = new Set<string>();
	for (const column of columns) {
		validateColumn(column);
		if (columnIds.has(column.id)) throw new Error(`DataTable duplicate column ID "${column.id}"`);
		columnIds.add(column.id);
	}
}

function validateRowIds<Row>(rows: readonly Row[], getRowId: (row: Row) => string): void {
	const rowIds = new Set<string>();
	for (const row of rows) {
		const id = getRowId(row);
		if (typeof id !== 'string' || !id.trim()) throw new Error('DataTable row IDs must not be empty');
		if (rowIds.has(id)) throw new Error(`DataTable duplicate row ID`);
		rowIds.add(id);
	}
}

function validateSortableTypes<Row>(
	rows: readonly Row[],
	columns: readonly DataTableColumn<Row>[]
): void {
	for (const column of columns) {
		if (column.sortValue === undefined) continue;
		let expectedType: string | undefined;
		for (const row of rows) {
			const value = column.sortValue(row);
			if (value === null) continue;
			const currentType = scalarType(value);
			if (expectedType !== undefined && currentType !== expectedType) {
				throw new Error(`DataTable column "${column.id}" returned mixed sortable scalar types`);
			}
			expectedType = currentType;
		}
	}
}

export function validateDataTable<Row>(
	rows: readonly Row[],
	columns: readonly DataTableColumn<Row>[],
	getRowId: (row: Row) => string,
	operations: DataTableOperations,
	initialSort: DataTableInitialSort | undefined,
	stickyHeaderOffset: number | undefined
): void {
	if (operations.kind === 'client-page') assertText('scope label', operations.scopeLabel);
	if (stickyHeaderOffset !== undefined && (!Number.isFinite(stickyHeaderOffset) || stickyHeaderOffset < 0)) {
		throw new Error('DataTable sticky header offset must be a non-negative finite number');
	}
	validateColumns(columns);
	if (initialSort !== undefined) {
		const initialColumn = columns.find((column) => column.id === initialSort.columnId);
		if (initialColumn?.sortValue === undefined) {
			throw new Error(`DataTable initial sort column "${initialSort.columnId}" is not sortable`);
		}
		if (initialSort.direction !== 'asc' && initialSort.direction !== 'desc') {
			throw new Error('DataTable initial sort direction is invalid');
		}
	}
	validateRowIds(rows, getRowId);
	validateSortableTypes(rows, columns);
}

function compareNonNull(left: Exclude<DataTableScalar, null>, right: Exclude<DataTableScalar, null>): number {
	if (typeof left === 'string' && typeof right === 'string') {
		const normalizedLeft = left.toLocaleLowerCase('en-US');
		const normalizedRight = right.toLocaleLowerCase('en-US');
		return normalizedLeft < normalizedRight ? -1 : normalizedLeft > normalizedRight ? 1 : 0;
	}
	if (typeof left === 'number' && typeof right === 'number') return left - right;
	if (typeof left === 'boolean' && typeof right === 'boolean') return Number(left) - Number(right);
	throw new Error('DataTable cannot compare mixed sortable scalar types');
}

export function sortRows<Row>(
	rows: readonly Row[],
	value: (row: Row) => DataTableScalar,
	direction: DataTableSortDirection
): Row[] {
	const factor = direction === 'asc' ? 1 : -1;
	return rows
		.map((row, index) => ({ row, index, value: value(row) }))
		.sort((left, right) => {
			if (left.value === null && right.value === null) return left.index - right.index;
			if (left.value === null) return 1;
			if (right.value === null) return -1;
			const compared = compareNonNull(left.value, right.value) * factor;
			return compared || left.index - right.index;
		})
		.map(({ row }) => row);
}

export function filterRows<Row>(
	rows: readonly Row[],
	columns: readonly DataTableColumn<Row>[],
	filters: Readonly<Record<string, string>>
): Row[] {
	const active = columns.flatMap((column) => {
		const query = filters[column.id]?.trim().toLocaleLowerCase('en-US') ?? '';
		return query && column.filterValue ? [{ query, value: column.filterValue }] : [];
	});
	if (!active.length) return [...rows];
	return rows.filter((row) =>
		active.every(({ query, value }) => {
			const candidate = value(row);
			return candidate !== null && String(candidate).trim().toLocaleLowerCase('en-US').includes(query);
		})
	);
}
