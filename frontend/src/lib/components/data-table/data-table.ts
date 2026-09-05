import type {
	DataTableColumn,
	DataTableInitialSort,
	DataTableOperations,
	DataTableScalar,
	DataTableSortDirection
} from './types';

class DataTableValidationError extends Error {}

function invalid(message: string): never {
	throw new DataTableValidationError(message);
}

function scalarType(value: Exclude<DataTableScalar, null>): string {
	return typeof value;
}

function assertText(name: string, value: string): void {
	if (!value.trim()) invalid(`DataTable ${name} must not be empty`);
}

function validateColumn<Row>(column: DataTableColumn<Row>): void {
	assertText('column ID', column.id);
	assertText(`column "${column.id}" label`, column.label);
	if (column.filter !== undefined) {
		assertText(`column "${column.id}" filter aria label`, column.filter.ariaLabel);
	}
	if (
		column.sticky !== undefined &&
		(!Number.isFinite(column.sticky.offset) || column.sticky.offset < 0)
	) {
		invalid(`DataTable column "${column.id}" has an invalid sticky offset`);
	}
}

function validateColumns<Row>(columns: readonly DataTableColumn<Row>[]): void {
	if (!columns.length) invalid('DataTable columns must not be empty');
	const columnIds = new Set<string>();
	for (const column of columns) {
		validateColumn(column);
		if (columnIds.has(column.id)) invalid(`DataTable duplicate column ID "${column.id}"`);
		columnIds.add(column.id);
	}
}

function validateRowIds<Row>(rows: readonly Row[], getRowId: (row: Row) => string): void {
	const rowIds = new Set<string>();
	for (const row of rows) {
		const id = getRowId(row);
		if (typeof id !== 'string' || !id.trim()) invalid('DataTable row IDs must not be empty');
		if (rowIds.has(id)) invalid('DataTable duplicate row ID');
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
			expectedType = validatedScalarType(column.id, column.sortValue(row), expectedType);
		}
	}
}

function validatedScalarType(
	columnId: string,
	value: DataTableScalar,
	expectedType: string | undefined
): string | undefined {
	if (value === null) return expectedType;
	if (typeof value === 'number' && !Number.isFinite(value)) {
		invalid(`DataTable column "${columnId}" requires finite numeric sort values`);
	}
	const currentType = scalarType(value);
	if (expectedType !== undefined && currentType !== expectedType) {
		invalid(`DataTable column "${columnId}" returned mixed sortable scalar types`);
	}
	return currentType;
}

export type DataTableValidation = { valid: true } | { valid: false; message: string };

function assertDataTable<Row>(
	rows: readonly Row[],
	columns: readonly DataTableColumn<Row>[],
	getRowId: (row: Row) => string,
	operations: DataTableOperations,
	initialSort: DataTableInitialSort | undefined,
	caption: string,
	regionLabel: string,
	emptyMessage: string
): void {
	assertText('caption', caption);
	assertText('region label', regionLabel);
	assertText('empty message', emptyMessage);
	if (operations.kind === 'client-page') assertText('scope label', operations.scopeLabel);
	validateColumns(columns);
	if (
		operations.kind === 'none' &&
		columns.some((column) => column.sortValue !== undefined || column.filter !== undefined)
	) {
		invalid('DataTable operations "none" cannot configure sorting or filtering');
	}
	if (initialSort !== undefined) {
		const initialColumn = columns.find((column) => column.id === initialSort.columnId);
		if (initialColumn?.sortValue === undefined) {
			invalid(`DataTable initial sort column "${initialSort.columnId}" is not sortable`);
		}
		if (initialSort.direction !== 'asc' && initialSort.direction !== 'desc') {
			invalid('DataTable initial sort direction is invalid');
		}
	}
	validateRowIds(rows, getRowId);
	validateSortableTypes(rows, columns);
}

export function validateDataTable<Row>(
	rows: readonly Row[],
	columns: readonly DataTableColumn<Row>[],
	getRowId: (row: Row) => string,
	operations: DataTableOperations,
	initialSort: DataTableInitialSort | undefined,
	caption: string,
	regionLabel: string,
	emptyMessage: string
): DataTableValidation {
	try {
		assertDataTable(rows, columns, getRowId, operations, initialSort, caption, regionLabel, emptyMessage);
		return { valid: true };
	} catch (error) {
		return {
			valid: false,
			message: error instanceof DataTableValidationError
				? error.message
				: 'DataTable row validation failed'
		};
	}
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
		return query && column.filter ? [{ query, value: column.filter.value }] : [];
	});
	if (!active.length) return [...rows];
	return rows.filter((row) =>
		active.every(({ query, value }) => {
			const candidate = value(row);
			return candidate !== null && String(candidate).trim().toLocaleLowerCase('en-US').includes(query);
		})
	);
}
