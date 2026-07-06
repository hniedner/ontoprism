<script lang="ts">
	import type { Snippet } from 'svelte';

	// Shared results wrapper for the repository search pages: the error banner and the
	// titled results card (header + count + loading state); the concrete results table
	// is passed as the default children snippet.
	interface Props {
		title: string;
		countLabel: string;
		loading: boolean;
		error: string | null;
		children: Snippet;
	}

	let { title, countLabel, loading, error, children }: Props = $props();
</script>

{#if error}
	<div
		class="rounded-xl border border-danger-200 bg-danger-50 p-4 text-sm text-danger dark:border-danger-800 dark:bg-danger-900/20"
	>
		Search failed: {error}
	</div>
{:else}
	<div class="overflow-hidden rounded-xl border border-default bg-card shadow-sm">
		<div class="flex items-center justify-between border-b border-default px-4 py-3">
			<h2 class="text-sm font-semibold text-default">{title}</h2>
			<span class="text-xs text-muted">{countLabel}</span>
		</div>
		{#if loading}
			<p class="px-4 py-6 text-center text-sm text-muted">Loading…</p>
		{:else}
			{@render children()}
		{/if}
	</div>
{/if}
