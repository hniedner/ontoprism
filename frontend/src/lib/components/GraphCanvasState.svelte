<script lang="ts">
	import LoadingState from '$lib/components/LoadingState.svelte';

	let {
		loading,
		error,
		visibleNodeCount,
		expanding,
		truncated
	}: {
		loading: boolean;
		error: string | null;
		visibleNodeCount: number;
		expanding: boolean;
		truncated: boolean;
	} = $props();
</script>

{#if loading}
	<div class="absolute inset-0">
		<LoadingState active label="Building graph" minHeight="100%" />
	</div>
{:else if error}
	<div class="absolute inset-0 flex items-center justify-center text-sm text-danger">
		{error}
	</div>
{:else if visibleNodeCount === 0}
	<div
		class="pointer-events-none absolute inset-0 flex items-center justify-center bg-card/85 px-6 text-center text-sm text-muted"
	>
		No graph nodes match the active filters.
	</div>
{/if}

{#if !loading && !error && truncated}
	<div
		role="status"
		class="pointer-events-none absolute bottom-3 left-3 rounded-md bg-warning-50 px-2 py-1 text-xs text-warning-800 shadow dark:bg-warning-950 dark:text-warning-200"
	>
		This graph is partial because the relationship limit was reached.
	</div>
{/if}

{#if expanding}
	<div
		class="absolute left-3 top-3 rounded-md bg-primary-600 px-2 py-1 text-xs font-medium text-white shadow"
	>
		<LoadingState active label="Expanding graph" minHeight="0" />
	</div>
{/if}
