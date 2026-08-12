<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { RemoteFailureState } from '$lib/api';
	import RemoteSearchState from '$lib/components/RemoteSearchState.svelte';

	interface Props {
		service: string;
		error: { remoteState: RemoteFailureState; message: string } | null;
		ready: boolean;
		instruction: Snippet;
		children: Snippet;
	}

	let { service, error, ready, instruction, children }: Props = $props();
</script>

{#if error}
	<RemoteSearchState {service} state={error.remoteState} message={error.message} />
{:else if ready}
	{@render children()}
{:else}
	<div class="rounded-xl border border-dashed border-default bg-card/50 px-6 py-12 text-center">
		{@render instruction()}
	</div>
{/if}
