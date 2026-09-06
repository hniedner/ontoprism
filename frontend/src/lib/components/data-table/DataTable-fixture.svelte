<script lang="ts">
	import DataTable from './DataTable.svelte';
	import type { DataTableColumn, DataTableFilterState, DataTableIntent, DataTableSortState } from './types';

	export interface TestRow { id: string; name: string | null; group: string | null; rank: number | null; active: boolean; }
	let { rows = [], sort = null, filters = {}, onintent = () => {}, emptyMessage = 'No records.', sticky = true, caption = 'Repository records', regionLabel = 'Repository records table' }: {
		rows?: readonly TestRow[];
		sort?: DataTableSortState | null;
		filters?: Readonly<Record<string, DataTableFilterState>>;
		onintent?: (intent: DataTableIntent) => void;
		emptyMessage?: string;
		sticky?: boolean;
		caption?: string;
		regionLabel?: string;
	} = $props();
	const filterStates = $derived({
		group: { kind: 'categorical', selected: [] } satisfies DataTableFilterState,
		active: { kind: 'categorical', selected: [] } satisfies DataTableFilterState,
		...filters
	});
	let columns = $derived.by((): readonly DataTableColumn<TestRow>[] => [
		{ id: 'name', label: 'Name', cell: nameCell, sortable: ['asc', 'desc'], sticky: sticky ? { side: 'left', offset: 0 } : undefined },
		{ id: 'group', label: 'Group', cell: groupCell, sortable: ['asc', 'desc'], filter: { kind: 'categorical', ariaLabel: 'Filter groups', options: [
			{ value: 'Current', label: 'Current' }, { value: 'Archived', label: 'Archived' }
		] } },
		{ id: 'rank', label: 'Rank', cell: rankCell },
		{ id: 'active', label: 'Active', cell: activeCell, filter: { kind: 'categorical', ariaLabel: 'Filter activity', options: [
			{ value: 'true', label: 'Active' }, { value: 'false', label: 'Inactive' }
		] } }
	]);
</script>

{#snippet nameCell(row: TestRow)}<span>{row.name ?? '—'}</span>{/snippet}
{#snippet groupCell(row: TestRow)}<strong>{row.group ?? '—'}</strong>{/snippet}
{#snippet rankCell(row: TestRow)}{row.rank ?? '—'}{/snippet}
{#snippet activeCell(row: TestRow)}{row.active ? 'Yes' : 'No'}{/snippet}

<DataTable {rows} {columns} {caption} {regionLabel} getRowId={(row) => row.id}
	operations={{ kind: 'server', sort, defaultSort: { key: 'name', direction: 'asc' }, activeSortLabel: sort?.direction === 'desc' ? 'Name descending' : 'Name ascending', filters: filterStates, busy: false, onintent }}
	{emptyMessage} stickyHeader={sticky} />
