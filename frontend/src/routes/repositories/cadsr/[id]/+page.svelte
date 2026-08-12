<script lang="ts">
	import { resolve } from '$app/paths';
	import CdeConceptGraph from './CdeConceptGraph.svelte';
	import CdeDetailCards from './CdeDetailCards.svelte';
	import CdeSummary from './CdeSummary.svelte';
	import type { PageProps } from './$types';
	import RepositoryKindBadge from '$lib/components/RepositoryKindBadge.svelte';

	let { data }: PageProps = $props();
	const cde = $derived(data.cde);
</script>

<svelte:head><title>{cde.long_name} · caDSR · ONTOPRISM</title></svelte:head>

<a
	href={resolve('/repositories/cadsr')}
	class="mb-4 inline-flex items-center gap-1.5 text-sm text-muted no-underline hover:text-primary-600"
>
	<span aria-hidden="true">←</span> Back to CDE search
</a>

<div class="mb-4"><RepositoryKindBadge kind="local-certified-proxy" /></div>

<CdeSummary {cde} />
<CdeConceptGraph {cde} />
<CdeDetailCards {cde} />
