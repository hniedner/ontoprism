<script lang="ts">
	// Shared search card for the repository browse/search pages (NCIt, caDSR,
	// ClinicalTrials): a labelled search input plus a row of quick-suggestion chips.
	interface Props {
		value: string;
		placeholder: string;
		ariaLabel: string;
		suggestions: string[];
		loading: boolean;
		onsearch: () => void;
		onsuggestion: (term: string) => void;
		suggestionsLabel?: string;
	}

	let {
		value = $bindable(),
		placeholder,
		ariaLabel,
		suggestions,
		loading,
		onsearch,
		onsuggestion,
		suggestionsLabel = 'Try:'
	}: Props = $props();

	function submit(event: SubmitEvent) {
		event.preventDefault();
		onsearch();
	}
</script>

<div class="mb-6 rounded-xl border border-default bg-card p-5 shadow-sm">
	<form onsubmit={submit} class="flex gap-2">
		<div class="relative flex-1">
			<svg
				viewBox="0 0 24 24"
				class="pointer-events-none absolute left-3 top-1/2 h-5 w-5 -translate-y-1/2 text-subtle"
				fill="none"
				stroke="currentColor"
				stroke-width="1.8"
			>
				<circle cx="11" cy="11" r="7" />
				<path d="m20 20-3.5-3.5" stroke-linecap="round" />
			</svg>
			<input
				type="search"
				bind:value={value}
				{placeholder}
				aria-label={ariaLabel}
				class="w-full rounded-lg border border-default bg-page-bg py-2.5 pl-10 pr-3 text-sm text-default placeholder:text-subtle focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-500/30 dark:bg-neutral-900"
			/>
		</div>
		<button
			type="submit"
			disabled={loading}
			class="rounded-lg bg-primary-600 px-5 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-primary-700 disabled:opacity-50"
		>
			{loading ? 'Searching…' : 'Search'}
		</button>
	</form>

	<div class="mt-3 flex flex-wrap items-center gap-2">
		<span class="text-xs font-medium text-muted">{suggestionsLabel}</span>
		{#each suggestions as s (s)}
			<button
				type="button"
				onclick={() => onsuggestion(s)}
				class="rounded-full bg-subtle px-3 py-1 text-xs text-secondary transition-colors hover:bg-primary-50 hover:text-primary-700 dark:hover:bg-primary-900/30 dark:hover:text-primary-300"
			>
				{s}
			</button>
		{/each}
	</div>
</div>
