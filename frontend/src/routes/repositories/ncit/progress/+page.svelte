<script lang="ts">
	import PublicationProgress from '$lib/components/PublicationProgress.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
</script>

<svelte:head><title>Publication progress · NCIt · ONTOPRISM</title></svelte:head>

{#if data.progress}
    <PublicationProgress progress={data.progress} />
{:else}
    <h1>Enhanced NCIt publication progress</h1>
    <p>No published run yet.</p>
{/if}
