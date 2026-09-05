import type { Snippet } from 'svelte';

export type DataTableScalar = string | number | boolean | null;
export type DataTableSortDirection = 'asc' | 'desc';

export interface DataTableStickyColumn {
	side: 'left';
	offset: number;
}

export interface DataTableFilter<Row> {
	value: (row: Row) => DataTableScalar;
	ariaLabel: string;
}

export interface DataTableColumn<Row> {
	id: string;
	label: string;
	cell: Snippet<[Row]>;
	sortValue?: (row: Row) => DataTableScalar;
	filter?: DataTableFilter<Row>;
	sticky?: DataTableStickyColumn;
}

export type DataTableOperations =
	| { kind: 'none' }
	| { kind: 'client-page'; scopeLabel: string };

export interface DataTableInitialSort {
	columnId: string;
	direction: DataTableSortDirection;
}

export interface DataTableReadyProps<Row> {
	rows: readonly Row[];
	columns: readonly DataTableColumn<Row>[];
	caption: string;
	regionLabel: string;
	getRowId: (row: Row) => string;
	operations: DataTableOperations;
	initialSort?: DataTableInitialSort;
	emptyMessage: string;
	stickyHeader: boolean;
}
