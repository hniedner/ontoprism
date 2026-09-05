import type { Snippet } from 'svelte';

export type DataTableScalar = string | number | boolean | null;
export type DataTableSortDirection = 'asc' | 'desc';

export interface DataTableStickyColumn {
	side: 'left';
	offset: number;
}

export type DataTableFilter<Row> =
	| { kind: 'text'; value: (row: Row) => DataTableScalar; ariaLabel: string }
	| {
			kind: 'categorical';
			value: (row: Row) => string | null;
			ariaLabel: string;
			emptyLabel?: string;
	  };

export type DataTableFilterState =
	| { kind: 'text'; query: string }
	| { kind: 'categorical'; selected: readonly (string | null)[] };

export interface DataTableCategoricalOption {
	value: string | null;
	label: string;
	count: number;
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
