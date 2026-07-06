<script lang="ts">
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import { getTrial } from '$lib/api.clinicaltrials';
	import type { CTStudyDetail } from '$lib/types';
	import TrialOverview from '$lib/components/trial/TrialOverview.svelte';
	import TrialProtocol from '$lib/components/trial/TrialProtocol.svelte';
	import TrialOutcomes from '$lib/components/trial/TrialOutcomes.svelte';
	import TrialSupport from '$lib/components/trial/TrialSupport.svelte';

	let trial = $state<CTStudyDetail | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);

	$effect(() => {
		const nct = page.params.nct;
		if (!nct) return;
		loading = true;
		error = null;
		trial = null;
		getTrial(nct)
			.then((t) => (trial = t))
			.catch((err) => (error = err instanceof Error ? err.message : String(err)))
			.finally(() => (loading = false));
	});
</script>

<svelte:head>
	<title>{trial?.title ?? page.params.nct} · ClinicalTrials.gov · ONTOPRISM</title>
</svelte:head>

<a
	href={resolve('/repositories/clinicaltrials')}
	class="mb-4 inline-flex items-center gap-1.5 text-sm text-muted no-underline hover:text-primary-600"
>
	<span aria-hidden="true">←</span> Back to trial search
</a>

{#if loading}
	<p class="text-sm text-muted">Loading {page.params.nct}…</p>
{:else if error}
	<div
		class="rounded-xl border border-danger-200 bg-danger-50 p-4 text-sm text-danger dark:border-danger-800 dark:bg-danger-900/20"
	>
		Failed to load trial: {error}
	</div>
{:else if trial}
	<div class="space-y-5">
		<TrialOverview {trial} />
		<TrialProtocol {trial} />
		<TrialOutcomes {trial} />
		<TrialSupport {trial} />
	</div>
{/if}
