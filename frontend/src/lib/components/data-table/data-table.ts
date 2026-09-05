import type { DataTableColumn, DataTableOperations } from './types';

class DataTableValidationError extends Error {}
function invalid(message: string): never { throw new DataTableValidationError(message); }
function text(name: string, value: string): void { if (!value.trim()) invalid(`DataTable ${name} must not be empty`); }

function validateCategoricalOptions<Row>(column: DataTableColumn<Row>): void {
	if (column.filter?.kind !== 'categorical') return;
	const values = new Set<string>();
	for (const option of column.filter.options) {
		text(`column "${column.id}" option value`, option.value);
		text(`column "${column.id}" option label`, option.label);
		if (values.has(option.value)) invalid(`DataTable column "${column.id}" has duplicate options`);
		values.add(option.value);
	}
}

function validateColumn<Row>(column: DataTableColumn<Row>, ids: Set<string>): void {
	text('column ID', column.id);
	text(`column "${column.id}" label`, column.label);
	if (ids.has(column.id)) invalid(`DataTable duplicate column ID "${column.id}"`);
	ids.add(column.id);
	if (column.filter) text(`column "${column.id}" filter aria label`, column.filter.ariaLabel);
	validateCategoricalOptions(column);
	if (column.sticky && (!Number.isFinite(column.sticky.offset) || column.sticky.offset < 0)) invalid(`DataTable column "${column.id}" has an invalid sticky offset`);
}

function validateServerOperations<Row>(columns: readonly DataTableColumn<Row>[], operations: Extract<DataTableOperations, { kind: 'server' }>): void {
	text('active sort label', operations.activeSortLabel);
	for (const state of [operations.sort, operations.defaultSort]) {
		const column = state && columns.find((candidate) => candidate.id === state.key);
		if (state && (!column?.sortable || !column.sortable.includes(state.direction))) invalid(`DataTable sort key "${state.key}" does not support ${state.direction}`);
	}
	for (const [id, state] of Object.entries(operations.filters)) {
		const filter = columns.find((column) => column.id === id)?.filter;
		if (!filter || filter.kind !== state.kind) invalid(`DataTable filter "${id}" is not configured`);
		if (filter?.kind === 'categorical') {
			const available = new Set(filter.options.map((option) => option.value));
			if (state.selected.some((value) => !available.has(value))) invalid(`DataTable filter "${id}" selected an invalid option`);
		}
	}
}

function validateOperations<Row>(columns: readonly DataTableColumn<Row>[], operations: DataTableOperations): void {
	if (operations.kind === 'server') validateServerOperations(columns, operations);
	else if (columns.some((column) => column.sortable || column.filter)) invalid('DataTable operations "none" cannot configure sorting or filtering');
}

function validateRows<Row>(rows: readonly Row[], getRowId: (row: Row) => string): void {
	const ids = new Set<string>();
	for (const row of rows) {
		const id = getRowId(row);
		if (typeof id !== 'string' || !id.trim()) invalid('DataTable row IDs must not be empty');
		if (ids.has(id)) invalid('DataTable duplicate row ID');
		ids.add(id);
	}
}

export type DataTableValidation = { valid: true } | { valid: false; message: string };

export function validateDataTable<Row>(
	rows: readonly Row[], columns: readonly DataTableColumn<Row>[], getRowId: (row: Row) => string,
	operations: DataTableOperations, caption: string, regionLabel: string, emptyMessage: string
): DataTableValidation {
	try {
		text('caption', caption); text('region label', regionLabel); text('empty message', emptyMessage);
		if (!columns.length) invalid('DataTable columns must not be empty');
		const columnIds = new Set<string>();
		for (const column of columns) validateColumn(column, columnIds);
		validateOperations(columns, operations);
		validateRows(rows, getRowId);
		return { valid: true };
	} catch (error) {
		return { valid: false, message: error instanceof DataTableValidationError ? error.message : 'DataTable row validation failed' };
	}
}
