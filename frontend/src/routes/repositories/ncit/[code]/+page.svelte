<script lang="ts">
	import { resolve } from '$app/paths';
	import DecompositionPanel from '$lib/components/DecompositionPanel.svelte';
	import ExternalMappingsPanel from '$lib/components/ExternalMappingsPanel.svelte';
	import MappedCdes from '$lib/components/MappedCdes.svelte';
	import NcitConceptGraph from './NcitConceptGraph.svelte';
	import NcitConceptSummary from './NcitConceptSummary.svelte';
	import RelationshipList from '$lib/components/RelationshipList.svelte';
	import SimilarConcepts from '$lib/components/SimilarConcepts.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
	const detail = $derived(data.detail);
</script>

<svelte:head><title>{detail.label ?? detail.code} · NCIt · ONTOPRISM</title></svelte:head>

<a
	href={resolve('/repositories/ncit')}
	class="mb-4 inline-flex items-center gap-1.5 text-sm text-muted no-underline hover:text-primary-600"
>
	<span aria-hidden="true">←</span> Back to search
</a>

<NcitConceptSummary {detail} />
<NcitConceptGraph code={detail.code} graph={data.graph} />

<div class="mt-6 grid gap-6 md:grid-cols-2 lg:grid-cols-3">
	<RelationshipList title="Roles" items={detail.roles} />
	<RelationshipList title="Associations" items={detail.associations} />
	<RelationshipList title="Incoming roles" items={detail.incoming_roles} />
	<DecompositionPanel code={detail.code} />
	<ExternalMappingsPanel code={detail.code} />
	<MappedCdes code={detail.code} />
	<SimilarConcepts code={detail.code} />
</div>
