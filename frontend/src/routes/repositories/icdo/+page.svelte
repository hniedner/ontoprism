<script lang="ts">
	import { resolve } from '$app/paths';
	import RepoPageHeader from '$lib/components/RepoPageHeader.svelte';
</script>

<svelte:head><title>ICD-O repositories | OntoPrism</title></svelte:head>
<section class="text-default">
	<RepoPageHeader
		title="ICD-O certified datasets"
		description="Edition- and axis-scoped morphology and topography repositories. Access requires ICD-O entitlement."
		kind="local-certified-proxy"
	/>
	<div class="mt-8 grid gap-4 md:grid-cols-3">
		<a class="rounded-xl border border-default bg-card p-5 shadow-sm transition-shadow hover:shadow-md" href={resolve('/repositories/icdo/[edition]/[axis]', { edition: '3.2', axis: 'morphology' })}><strong>ICD-O-3.2 morphology</strong></a>
		<a class="rounded-xl border border-default bg-card p-5 shadow-sm transition-shadow hover:shadow-md" href={resolve('/repositories/icdo/[edition]/[axis]', { edition: '4.0', axis: 'morphology' })}><strong>ICD-O-4 morphology</strong></a>
		<a class="rounded-xl border border-default bg-card p-5 shadow-sm transition-shadow hover:shadow-md" href={resolve('/repositories/icdo/[edition]/[axis]', { edition: '4.0', axis: 'topography' })}><strong>ICD-O-4 topography</strong></a>
	</div>
</section>
