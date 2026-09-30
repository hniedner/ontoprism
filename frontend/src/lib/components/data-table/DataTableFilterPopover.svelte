<script lang="ts">
	import { onDestroy, untrack } from 'svelte';
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
	const textApplicable = $derived(filter.kind === 'text' || filter.textFilter === true);
	let draft = $state(untrack(() => applied));
	let editing = false;
	let inputError = $state('');
	$effect(() => {
		const echoed = applied;
		untrack(() => { if (!editing) draft = echoed; else if (draft.trim() === echoed) editing = false; });
	});
	const options = $derived(filter.kind === 'categorical' ? filter.options.filter((option) => option.label.toLowerCase().includes(draft.toLowerCase())) : []);
	let pending: ReturnType<typeof setTimeout> | undefined;
	function cancelPending(): void { if (pending !== undefined) clearTimeout(pending); pending = undefined; }
	onDestroy(cancelPending);
	function scheduleText(): void {
		editing = true;
		cancelPending();
		if (!textApplicable) return;
		pending = setTimeout(() => { pending = undefined; applyText(); }, 350);
	}
	function applyText(): void {
		cancelPending();
		const text = draft.trim();
		if (!textApplicable) return;
		inputError = text.length > 100 || [...text].some((char) => char.charCodeAt(0) < 32) ? 'Use at most 100 characters without control characters.' : '';
		if (inputError) return;
		if (text === applied) return;
		onintent({ kind: 'filter', columnId, filter: filter.kind === 'categorical'
			? { kind: 'categorical', selected, text }
			: { kind: 'text', text } });
	}

	function toggle(option: string): void {
		cancelPending();
		const next = selected.includes(option)
			? selected.filter((value) => value !== option)
			: [...selected, option];
		onintent({
			kind: 'filter',
			columnId,
			filter: textApplicable && draft.trim() ? { kind: 'categorical', selected: next, text: draft.trim() } : { kind: 'categorical', selected: next }
		});
	}
</script>

<div
	role="dialog"
	aria-label={`${columnLabel} filter`}
	class="min-w-56 rounded-md border border-default bg-card p-3 text-left font-normal normal-case tracking-normal text-default shadow-lg"
>
	<form onsubmit={(event) => { event.preventDefault(); applyText(); }}>
		<label for={`filter-${columnId}`} class="text-xs font-semibold text-muted">{textApplicable ? `Filter ${columnLabel} text` : `Find ${columnLabel} options`}</label>
		<input id={`filter-${columnId}`} aria-describedby={`filter-help-${columnId}`} type="text" maxlength="100" bind:value={draft} oninput={scheduleText} class="block w-full rounded border border-default bg-card p-1 text-sm" />
		<span id={`filter-help-${columnId}`} class="sr-only">{textApplicable ? filter.ariaLabel : 'Narrows the options only; select a checkbox to filter rows.'}</span>
	</form>
	{#if inputError}<p role="alert" class="text-xs text-danger">{inputError}</p>{/if}
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
		onclick={() => { cancelPending(); editing = false; draft = ''; onintent({ kind: 'clear-filter', columnId }); }}
	>
		Clear filter for {columnLabel}
	</button>
</div>
