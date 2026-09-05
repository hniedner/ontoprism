<script lang="ts">
	import type { DataTableColumn, DataTableOperations, DataTableState } from './types';
	import DataTable from './DataTable.svelte';

	export interface TestRow {
		id: string;
		name: string | null;
		group: string;
		rank: number | null;
		active: boolean;
	}

	interface Props {
		rows?: readonly TestRow[];
		operations?: DataTableOperations;
		state?: DataTableState;
		caption?: string;
		regionLabel?: string;
		emptyMessage?: string;
		duplicateColumns?: boolean;
		sticky?: boolean;
		invalidSticky?: boolean;
		invalidFilter?: boolean;
		initialSort?: { columnId: string; direction: 'asc' | 'desc' };
		rowId?: (row: TestRow) => string;
	}

	let {
		rows = [],
		operations = { kind: 'client-page', scopeLabel: 'Filters and sorting apply to this loaded page.' },
		state = { kind: 'ready' },
		caption = 'Loaded records',
		regionLabel = 'Loaded records table',
		emptyMessage = 'No source records.',
		duplicateColumns = false,
		sticky = false,
		invalidSticky = false,
		invalidFilter = false,
		initialSort,
		rowId = (row) => row.id
	}: Props = $props();

	let columns = $derived.by((): readonly DataTableColumn<TestRow>[] => {
		const result: DataTableColumn<TestRow>[] = [
			{
				id: 'name',
				label: 'Name',
				cell: nameCell,
				sortValue: (row) => row.name,
				filterValue: (row) => row.name,
				filterAriaLabel: 'Filter loaded names',
				filterPlaceholder: 'Filter names',
				sticky: sticky
					? { side: 'left', offset: invalidSticky ? -1 : 0 }
					: undefined
			},
			{
				id: duplicateColumns ? 'name' : 'group',
				label: 'Group',
				cell: groupCell,
				sortValue: (row) => row.group,
				filterValue: invalidFilter ? undefined : (row) => row.group,
				filterAriaLabel: invalidFilter ? 'Broken filter' : 'Filter loaded groups',
				sticky: sticky ? { side: 'left', offset: 120 } : undefined
			},
			{
				id: 'rank',
				label: 'Rank',
				cell: rankCell,
				sortValue: (row) => row.rank,
				sticky: sticky ? { side: 'right', offset: 0 } : undefined
			},
			{
				id: 'active',
				label: 'Active',
				cell: activeCell,
				sortValue: (row) => row.active
			}
		];
		return result;
	});
</script>

{#snippet nameCell(row: TestRow)}
	<span>{row.name ?? '—'}</span>
{/snippet}
{#snippet groupCell(row: TestRow)}
	<strong>{row.group}</strong>
{/snippet}
{#snippet rankCell(row: TestRow)}
	{row.rank ?? '—'}
{/snippet}
{#snippet activeCell(row: TestRow)}
	{row.active ? 'Yes' : 'No'}
{/snippet}

<DataTable
	{rows}
	{columns}
	{caption}
	{regionLabel}
	getRowId={rowId}
	{operations}
	{state}
	{emptyMessage}
	{initialSort}
	stickyHeaderOffset={sticky ? 0 : undefined}
/>
