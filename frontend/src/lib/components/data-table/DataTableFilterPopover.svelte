<script lang="ts">
	import type { DataTableFilter, DataTableFilterState, DataTableIntent } from './types';

	let {
		columnId,
		columnLabel,
		filter,
		value,
		onintent
	}: {
		columnId: string;
		columnLabel: string;
		filter: DataTableFilter;
		value: DataTableFilterState | undefined;
		onintent: (intent: DataTableIntent) => void;
	} = $props();

	const selected = $derived(value?.selected ?? []);

	function toggle(option: string): void {
		const next = selected.includes(option)
			? selected.filter((value) => value !== option)
			: [...selected, option];
		onintent({
			kind: 'filter',
			columnId,
			filter: { kind: 'categorical', selected: next }
		});
	}
</script>

<div
	role="dialog"
	aria-label={`${columnLabel} filter`}
	class="min-w-56 rounded-md border border-default bg-card p-3 text-left font-normal normal-case tracking-normal text-default shadow-lg"
>
	<fieldset class="space-y-2">
		<legend class="text-xs font-semibold text-muted">{filter.ariaLabel}</legend>
		{#each filter.options as option (option.value)}
			<label class="flex cursor-pointer items-center gap-2 whitespace-nowrap text-sm">
				<input
					type="checkbox"
					checked={selected.includes(option.value)}
					aria-label={option.label}
					onchange={() => toggle(option.value)}
				/>
				<span>{option.label}</span>
			</label>
		{/each}
	</fieldset>
	<button
		type="button"
		class="mt-3 text-xs underline disabled:cursor-not-allowed disabled:opacity-50"
		disabled={selected.length === 0}
		onclick={() => onintent({ kind: 'clear-filter', columnId })}
	>
		Clear selections for {columnLabel}
	</button>
</div>
