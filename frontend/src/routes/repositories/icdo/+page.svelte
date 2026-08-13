<script lang="ts">
	import { resolve } from '$app/paths';
	import RepositoryKindBadge from '$lib/components/RepositoryKindBadge.svelte';
</script>

<svelte:head><title>ICD-O repositories | OntoPrism</title></svelte:head>
<section class="mx-auto max-w-5xl px-6 py-10">
	<div class="mb-4"><RepositoryKindBadge kind="local-certified-proxy" /></div>
	<h1 class="text-3xl font-bold">ICD-O certified datasets</h1>
	<p class="mt-3 text-secondary">Edition- and axis-scoped morphology and topography repositories. Access requires ICD-O entitlement.</p>
	<div class="mt-8 grid gap-4 md:grid-cols-3">
		<a class="rounded-xl border border-default bg-card p-5" href={resolve('/repositories/icdo/[edition]/[axis]', { edition: '3.2', axis: 'morphology' })}><strong>ICD-O-3.2 morphology</strong></a>
		<a class="rounded-xl border border-default bg-card p-5" href={resolve('/repositories/icdo/[edition]/[axis]', { edition: '4.0', axis: 'morphology' })}><strong>ICD-O-4 morphology</strong></a>
		<a class="rounded-xl border border-default bg-card p-5" href={resolve('/repositories/icdo/[edition]/[axis]', { edition: '4.0', axis: 'topography' })}><strong>ICD-O-4 topography</strong></a>
	</div>
</section>
