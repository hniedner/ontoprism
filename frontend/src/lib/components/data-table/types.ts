import type { Snippet } from 'svelte';

export type DataTableScalar = string | number | boolean | null;
export type DataTableSortDirection = 'asc' | 'desc';

export interface DataTableStickyColumn {
	side: 'left' | 'right';
	offset: number;
}

export interface DataTableColumn<Row> {
	id: string;
	label: string;
	cell: Snippet<[Row]>;
	sortValue?: (row: Row) => DataTableScalar;
	filterValue?: (row: Row) => DataTableScalar;
	filterAriaLabel?: string;
	filterPlaceholder?: string;
	sticky?: DataTableStickyColumn;
	headerClass?: string;
	cellClass?: string;
}

export type DataTableOperations =
	| { kind: 'none' }
	| { kind: 'client-page'; scopeLabel: string };

export type DataTableState =
	| { kind: 'ready' }
	| { kind: 'loading'; label?: string }
	| { kind: 'error'; message: string };

export interface DataTableInitialSort {
	columnId: string;
	direction: DataTableSortDirection;
}
