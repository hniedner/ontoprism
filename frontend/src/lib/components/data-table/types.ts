import type { Snippet } from 'svelte';

export type DataTableSortDirection = 'asc' | 'desc';

export interface DataTableStickyColumn {
	side: 'left';
	offset: number;
}

export interface DataTableCategoricalOption {
	value: string;
	label: string;
}

export type DataTableFilter = { kind: 'categorical'; ariaLabel: string; options: readonly DataTableCategoricalOption[] };

export type DataTableFilterState = { kind: 'categorical'; selected: readonly string[] };

export interface DataTableSortState {
	key: string;
	direction: DataTableSortDirection;
}

export type DataTableIntent =
	| { kind: 'sort'; sort: DataTableSortState }
	| { kind: 'filter'; columnId: string; filter: DataTableFilterState }
	| { kind: 'clear-filter'; columnId: string }
	| { kind: 'clear-filters' }
	| { kind: 'reset' };

export interface DataTableColumn<Row> {
	id: string;
	label: string;
	cell: Snippet<[Row]>;
	sortable?: readonly DataTableSortDirection[];
	filter?: DataTableFilter;
	sticky?: DataTableStickyColumn;
}

export type DataTableOperations =
	| { kind: 'none' }
	| {
			kind: 'server';
			sort: DataTableSortState | null;
			defaultSort: DataTableSortState | null;
			activeSortLabel: string;
			filters: Readonly<Record<string, DataTableFilterState>>;
			busy: boolean;
			onintent: (intent: DataTableIntent) => void;
	  };

export interface DataTableReadyProps<Row> {
	rows: readonly Row[];
	columns: readonly DataTableColumn<Row>[];
	caption: string;
	regionLabel: string;
	getRowId: (row: Row) => string;
	operations: DataTableOperations;
	emptyMessage: string;
	stickyHeader: boolean;
}
