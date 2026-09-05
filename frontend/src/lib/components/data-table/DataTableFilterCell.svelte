<script lang="ts" generics="Row">
	import type { DataTableColumn, DataTableFilterState, DataTableIntent } from './types';
	let { column, value, className, style, onintent }: { column: DataTableColumn<Row>; value: DataTableFilterState | undefined; className: string; style?: string; onintent: (intent: DataTableIntent) => void } = $props();
	let timer: ReturnType<typeof setTimeout> | undefined;
	const selected = $derived(value?.kind === 'categorical' ? value.selected : []);
	function text(query: string): void { clearTimeout(timer); timer = setTimeout(() => onintent({ kind: 'filter', columnId: column.id, filter: { kind: 'text', query } }), 300); }
	function toggle(option: string): void { const next = selected.includes(option) ? selected.filter((value) => value !== option) : [...selected, option]; onintent({ kind: 'filter', columnId: column.id, filter: { kind: 'categorical', selected: next } }); }
</script>
<td class={className} {style}>
	{#if column.filter?.kind === 'text'}<input type="search" class="w-full min-w-28 rounded border border-default bg-card px-2 py-1 font-normal normal-case tracking-normal text-default" aria-label={column.filter.ariaLabel} value={value?.kind === 'text' ? value.query : ''} oninput={(event) => text(event.currentTarget.value)} />
	{:else if column.filter?.kind === 'categorical'}<fieldset class="min-w-36 space-y-1 font-normal normal-case tracking-normal text-default"><legend class="sr-only">{column.filter.ariaLabel}</legend>{#each column.filter.options as option (option.value)}<label class="flex cursor-pointer items-center gap-1.5 whitespace-nowrap"><input type="checkbox" checked={selected.includes(option.value)} aria-label={`${option.label}${option.count === undefined ? '' : ` (${option.count})`}`} onchange={() => toggle(option.value)} /><span>{option.label}{#if option.count !== undefined} <span class="text-subtle">({option.count})</span>{/if}</span></label>{/each}</fieldset>{/if}
</td>
