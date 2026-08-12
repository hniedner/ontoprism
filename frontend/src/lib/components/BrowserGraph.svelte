<script lang="ts">
	import { onMount } from 'svelte';
	import type { Component } from 'svelte';
	import type { Neighborhood } from '$lib/types';
	import LoadingState from '$lib/components/LoadingState.svelte';

	interface Props {
		code: string;
		initial?: Neighborhood | null;
		height?: string;
		loader?: () => Promise<{ default: Component<GraphProps> }>;
	}
	type GraphProps = Omit<Props, 'loader'>;

	let {
		code,
		initial = null,
		height = '32rem',
		loader = () => import('$lib/components/GraphExplorer.svelte')
	}: Props = $props();
	let GraphComponent = $state<Component<GraphProps> | null>(null);
	let failed = $state(false);

	onMount(async () => {
		try {
			GraphComponent = (await loader()).default;
		} catch {
			failed = true;
		}
	});
</script>

{#if GraphComponent}
	<GraphComponent {code} {initial} {height} />
{:else if failed}
	<div
		role="alert"
		class="flex items-center justify-center rounded-xl border border-default text-sm text-danger-600 dark:text-danger-400"
		style:min-height={height}
	>
		Concept graph is unavailable right now.
	</div>
{:else}
	<LoadingState active label="Loading concept graph" delayMs={0} minHeight={height} />
{/if}
