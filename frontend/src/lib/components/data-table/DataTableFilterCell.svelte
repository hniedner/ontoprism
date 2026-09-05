<script lang="ts" generics="Row">
	import type { DataTableColumn } from './types';

	let { column, value, className, style, onfilter }: {
		column: DataTableColumn<Row>;
		value: string;
		className: string;
		style?: string;
		onfilter: (columnId: string, value: string) => void;
	} = $props();
</script>

<td class={className} {style}>
	{#if column.filter}
		<input type="search" class="w-full min-w-28 rounded border border-default bg-card px-2 py-1 font-normal normal-case tracking-normal text-default" aria-label={column.filter.ariaLabel} placeholder={column.filter.placeholder} {value} oninput={(event) => onfilter(column.id, event.currentTarget.value)} />
	{/if}
</td>
