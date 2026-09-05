<script lang="ts" generics="Row">
	import { categoricalOptions } from './data-table';
	import type { DataTableColumn, DataTableFilterState } from './types';

	let { rows, column, value, className, style, onfilter }: {
		rows: readonly Row[];
		column: DataTableColumn<Row>;
		value: DataTableFilterState | undefined;
		className: string;
		style?: string;
		onfilter: (columnId: string, value: DataTableFilterState) => void;
	} = $props();

	let options = $derived(categoricalOptions(rows, column));
	let selected = $derived(value?.kind === 'categorical' ? value.selected : []);

	function isSelected(option: string | null): boolean {
		return selected.some((candidate) => Object.is(candidate, option));
	}

	function toggle(option: string | null): void {
		const next = isSelected(option)
			? selected.filter((candidate) => !Object.is(candidate, option))
			: [...selected, option];
		onfilter(column.id, { kind: 'categorical', selected: next });
	}
</script>

<td class={className} {style}>
	{#if column.filter?.kind === 'text'}
		<input type="search" class="w-full min-w-28 rounded border border-default bg-card px-2 py-1 font-normal normal-case tracking-normal text-default" aria-label={column.filter.ariaLabel} value={value?.kind === 'text' ? value.query : ''} oninput={(event) => onfilter(column.id, { kind: 'text', query: event.currentTarget.value })} />
	{:else if column.filter?.kind === 'categorical'}
		<fieldset class="min-w-36 space-y-1 font-normal normal-case tracking-normal text-default">
			<legend class="sr-only">{column.filter.ariaLabel}</legend>
			{#each options as option (option.value)}
				<label class="flex cursor-pointer items-center gap-1.5 whitespace-nowrap">
					<input type="checkbox" checked={isSelected(option.value)} aria-label={`${option.label} (${option.count})`} onchange={() => toggle(option.value)} />
					<span>{option.label} <span class="text-subtle">({option.count})</span></span>
				</label>
			{/each}
		</fieldset>
	{/if}
</td>
