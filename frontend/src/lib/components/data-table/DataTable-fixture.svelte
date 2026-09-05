<script lang="ts">
	import type { DataTableColumn, DataTableOperations } from './types';
	import DataTable from './DataTable.svelte';

	export interface TestRow {
		id: string;
		name: string | null;
		group: string | null;
		rank: number | null;
		active: boolean;
	}

	interface Props {
		rows?: readonly TestRow[];
		operations?: DataTableOperations;
		caption?: string;
		regionLabel?: string;
		emptyMessage?: string;
		emptyColumns?: boolean;
		duplicateColumns?: boolean;
		sticky?: boolean;
		invalidSticky?: boolean;
		invalidFilter?: boolean;
		controls?: boolean;
		initialSort?: { columnId: string; direction: 'asc' | 'desc' };
		rowId?: (row: TestRow) => string;
	}

	let {
		rows = [],
		operations = { kind: 'client-page', scopeLabel: 'Filters and sorting apply to this loaded page.' },
		caption = 'Loaded records',
		regionLabel = 'Loaded records table',
		emptyMessage = 'No source records.',
		emptyColumns = false,
		duplicateColumns = false,
		sticky = false,
		invalidSticky = false,
		invalidFilter = false,
		controls = true,
		initialSort,
		rowId = (row) => row.id
	}: Props = $props();

	let columns = $derived.by((): readonly DataTableColumn<TestRow>[] => {
		if (emptyColumns) return [];
		const result: DataTableColumn<TestRow>[] = [
			{
				id: 'name',
				label: 'Name',
				cell: nameCell,
				sortValue: controls ? (row) => row.name : undefined,
				filter: controls ? { kind: 'text', value: (row) => row.name, ariaLabel: 'Filter loaded names' } : undefined,
				sticky: sticky
					? { side: 'left', offset: invalidSticky ? -1 : 0 }
					: undefined
			},
			{
				id: duplicateColumns ? 'name' : 'group',
				label: 'Group',
				cell: groupCell,
				sortValue: controls ? (row) => row.group : undefined,
				filter: controls
					? { kind: 'categorical', value: (row) => row.group, ariaLabel: invalidFilter ? ' ' : 'Filter loaded groups', emptyLabel: 'No group' }
					: undefined
			},
			{
				id: 'rank',
				label: 'Rank',
				cell: rankCell,
				sortValue: controls ? (row) => row.rank : undefined,
				sticky: undefined
			},
			{
				id: 'active',
				label: 'Active',
				cell: activeCell,
				sortValue: controls ? (row) => row.active : undefined
			}
		];
		return result;
	});
</script>

{#snippet nameCell(row: TestRow)}
	<span>{row.name ?? '—'}</span>
{/snippet}
{#snippet groupCell(row: TestRow)}
	<strong>{row.group ?? '—'}</strong>
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
	{emptyMessage}
	{initialSort}
	stickyHeader={sticky}
/>
