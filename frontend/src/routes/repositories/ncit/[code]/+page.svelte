<script lang="ts">
	import { resolve } from '$app/paths';
	import DecompositionPanel from '$lib/components/DecompositionPanel.svelte';
	import AlignmentLinks from '$lib/components/AlignmentLinks.svelte';
	import ExternalMappingsPanel from '$lib/components/ExternalMappingsPanel.svelte';
	import MappedCdes from '$lib/components/MappedCdes.svelte';
	import NcitConceptGraph from './NcitConceptGraph.svelte';
	import NcitConceptSummary from './NcitConceptSummary.svelte';
	import RelationshipList from '$lib/components/RelationshipList.svelte';
	import SimilarConcepts from '$lib/components/SimilarConcepts.svelte';
	import type { PageProps } from './$types';
	import RepositoryKindBadge from '$lib/components/RepositoryKindBadge.svelte';

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

<div class="mb-4"><RepositoryKindBadge kind="local-certified-proxy" /></div>

<NcitConceptSummary {detail} />
<div class="mt-6">
	<AlignmentLinks
		title="Aligned Uberon/CL concepts"
		alignments={data.mappings.mappings
			.filter((mapping) => mapping.system === 'uberon-cl')
			.map((mapping) => ({
				code: mapping.object_id,
				system: 'uberon-cl' as const,
				version: mapping.version,
				predicate: mapping.predicate,
				lifecycle: mapping.lifecycle
			}))}
/>
</div>
<div class="mt-6">
	<AlignmentLinks
		title="Aligned ICD-O-3.2 morphology codes"
		alignments={data.mappings.mappings
			.filter((mapping) => mapping.system === 'icdo' && mapping.version === '3.2')
			.map((mapping) => ({
				code: mapping.object_id,
				system: 'icdo' as const,
				version: mapping.version,
				predicate: mapping.predicate,
				lifecycle: mapping.lifecycle
			}))}
	/>
</div>
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
