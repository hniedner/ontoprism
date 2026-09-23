<script lang="ts">
	import PublicationProgress from '$lib/components/PublicationProgress.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
</script>

<svelte:head><title>Publication progress · NCIt · ONTOPRISM</title></svelte:head>

<PublicationProgress progress={data.progress} />
