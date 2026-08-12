<script lang="ts">
	import { resolve } from '$app/paths';
	import TrialOverview from '$lib/components/trial/TrialOverview.svelte';
	import TrialProtocol from '$lib/components/trial/TrialProtocol.svelte';
	import TrialOutcomes from '$lib/components/trial/TrialOutcomes.svelte';
	import TrialSupport from '$lib/components/trial/TrialSupport.svelte';
	import type { PageProps } from './$types';
	import RepositoryKindBadge from '$lib/components/RepositoryKindBadge.svelte';
	import RemoteServiceDisclosure from '$lib/components/RemoteServiceDisclosure.svelte';

	let { data }: PageProps = $props();
	const trial = $derived(data.trial);
</script>

<svelte:head>
	<title>{trial.title} · ClinicalTrials.gov · ONTOPRISM</title>
</svelte:head>

<a
	href={resolve('/repositories/clinicaltrials')}
	class="mb-4 inline-flex items-center gap-1.5 text-sm text-muted no-underline hover:text-primary-600"
>
	<span aria-hidden="true">←</span> Back to trial search
</a>

<div class="mb-4"><RepositoryKindBadge kind="remote-live-service" /></div>
<RemoteServiceDisclosure service="ClinicalTrials.gov" />

	<div class="space-y-5">
		<TrialOverview {trial} />
		<TrialProtocol {trial} />
		<TrialOutcomes {trial} />
		<TrialSupport {trial} />
	</div>
