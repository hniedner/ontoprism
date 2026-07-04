<script lang="ts">
	import type { Snippet } from 'svelte';

	interface Props {
		title: string;
		description: string;
		/** Total item count for the repository, shown as a chip. */
		total?: number | null;
		help?: Snippet;
	}

	let { title, description, total = null, help }: Props = $props();

	let showHelp = $state(false);
</script>

<div class="mb-6">
	<div class="flex flex-wrap items-start justify-between gap-4">
		<div class="min-w-0">
			<div class="flex items-center gap-3">
				<h1 class="text-2xl font-semibold text-default">{title}</h1>
				{#if total != null}
					<span
						class="rounded-full bg-primary-50 px-2.5 py-0.5 text-xs font-medium text-primary-700 dark:bg-primary-900/30 dark:text-primary-300"
					>
						{total.toLocaleString()} total
					</span>
				{/if}
			</div>
			<p class="mt-1 max-w-3xl text-sm text-muted">{description}</p>
		</div>

		{#if help}
			<button
				type="button"
				onclick={() => (showHelp = !showHelp)}
				class="inline-flex items-center gap-1.5 rounded-lg border border-default bg-card px-3 py-1.5 text-sm font-medium text-secondary transition-colors hover:bg-subtle"
			>
				<svg viewBox="0 0 24 24" class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="1.8">
					<circle cx="12" cy="12" r="9" />
					<path d="M9.5 9a2.5 2.5 0 1 1 3 2.5c-.7.3-1 .8-1 1.5v.5" stroke-linecap="round" />
					<circle cx="12" cy="17" r="0.6" fill="currentColor" stroke="none" />
				</svg>
				Help
			</button>
		{/if}
	</div>

	{#if showHelp && help}
		<div
			class="mt-3 rounded-lg border border-info-200 bg-info-50 p-4 text-sm text-secondary dark:border-info-800 dark:bg-info-900/20"
		>
			{@render help()}
		</div>
	{/if}
</div>
