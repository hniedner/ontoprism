<script lang="ts" generics="Row">
	import type { DataTableColumn, DataTableFilterState, DataTableIntent } from './types';
	let { column, value, className, style, onintent }: { column: DataTableColumn<Row>; value: DataTableFilterState | undefined; className: string; style?: string; onintent: (intent: DataTableIntent) => void } = $props();
	const selected = $derived(value?.selected ?? []);
	function toggle(option: string): void { const next = selected.includes(option) ? selected.filter((value) => value !== option) : [...selected, option]; onintent({ kind: 'filter', columnId: column.id, filter: { kind: 'categorical', selected: next } }); }
</script>
<td class={className} {style}>
	{#if column.filter}<fieldset class="min-w-36 space-y-1 font-normal normal-case tracking-normal text-default"><legend class="sr-only">{column.filter.ariaLabel}</legend>{#each column.filter.options as option (option.value)}<label class="flex cursor-pointer items-center gap-1.5 whitespace-nowrap"><input type="checkbox" checked={selected.includes(option.value)} aria-label={option.label} onchange={() => toggle(option.value)} /><span>{option.label}</span></label>{/each}</fieldset>{/if}
</td>
