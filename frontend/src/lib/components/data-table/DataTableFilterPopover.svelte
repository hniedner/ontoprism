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

	const selected = $derived(value?.kind === 'categorical' ? value.selected : []);
	const applied = $derived(value?.text ?? '');
	let draft = $derived(applied);
	const options = $derived(filter.kind === 'categorical' ? filter.options.filter((option) => option.label.toLowerCase().includes(draft.toLowerCase())) : []);
	function applyText(): void {
		const text = draft.trim();
		if (filter.kind === 'categorical' && !filter.textFilter) return;
		if (text.length > 100 || [...text].some((char) => char.charCodeAt(0) < 32)) return;
		onintent({ kind: 'filter', columnId, filter: filter.kind === 'categorical'
			? { kind: 'categorical', selected, text }
			: { kind: 'text', text } });
	}

	function toggle(option: string): void {
		const next = selected.includes(option)
			? selected.filter((value) => value !== option)
			: [...selected, option];
		onintent({
			kind: 'filter',
			columnId,
			filter: applied ? { kind: 'categorical', selected: next, text: applied } : { kind: 'categorical', selected: next }
		});
	}
</script>

<div
	role="dialog"
	aria-label={`${columnLabel} filter`}
	class="min-w-56 rounded-md border border-default bg-card p-3 text-left font-normal normal-case tracking-normal text-default shadow-lg"
>
	<form onsubmit={(event) => { event.preventDefault(); applyText(); }}>
		<label for={`filter-${columnId}`} class="text-xs font-semibold text-muted">Filter {columnLabel} text</label>
		<input id={`filter-${columnId}`} type="text" maxlength="100" bind:value={draft} class="block w-full rounded border border-default bg-card p-1 text-sm" />
		{#if filter.kind === 'text' || filter.textFilter}
			<button type="submit" class="mt-1 text-xs underline">Apply {columnLabel} text</button>
		{/if}
	</form>
	{#if filter.kind === 'categorical'}
		<fieldset class="mt-2 space-y-2">
			<legend class="text-xs font-semibold text-muted">{filter.ariaLabel}</legend>
			{#each options as option (option.value)}
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
	{/if}
	<button
		type="button"
		class="mt-3 text-xs underline disabled:cursor-not-allowed disabled:opacity-50"
		disabled={selected.length === 0 && !applied}
		onclick={() => onintent({ kind: 'clear-filter', columnId })}
	>
		Clear filter for {columnLabel}
	</button>
</div>
