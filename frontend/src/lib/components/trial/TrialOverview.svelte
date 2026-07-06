<script lang="ts">
	import type { CTStudyDetail } from '$lib/types';

	let { trial }: { trial: CTStudyDetail } = $props();

	const sectionTitle = 'mb-2 text-xs font-semibold uppercase tracking-wide text-muted';
</script>

<div class="rounded-xl border border-default bg-card p-5 shadow-sm">
	<div class="flex flex-wrap items-center gap-2">
		<span
			class="rounded bg-subtle px-1.5 py-0.5 font-mono text-xs text-primary-600 dark:text-primary-400"
			>{trial.nct_id}</span
		>
		{#if trial.status}<span class="text-xs text-muted">{trial.status}</span>{/if}
		{#if trial.phase}
			<span class="rounded-md bg-info-50 px-2 py-0.5 text-xs font-medium text-info dark:bg-info-900/30"
				>{trial.phase}</span
			>
		{/if}
	</div>
	<h1 class="mt-2 text-lg font-semibold text-default">{trial.title}</h1>
	{#if trial.official_title && trial.official_title !== trial.title}
		<p class="mt-1 text-sm text-muted">{trial.official_title}</p>
	{/if}
	<dl class="mt-4 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
		<div>
			<dt class={sectionTitle}>Study type</dt>
			<dd class="text-default">{trial.study_type ?? '—'}</dd>
		</div>
		<div>
			<dt class={sectionTitle}>Purpose</dt>
			<dd class="text-default">{trial.primary_purpose ?? '—'}</dd>
		</div>
		<div>
			<dt class={sectionTitle}>Enrollment</dt>
			<dd class="text-default">{trial.enrollment ?? '—'}</dd>
		</div>
		<div>
			<dt class={sectionTitle}>Start</dt>
			<dd class="text-default">{trial.start_date ?? '—'}</dd>
		</div>
	</dl>
	<!-- External absolute URL (clinicaltrials.gov); resolve() is for internal routes only. -->
	<!-- eslint-disable svelte/no-navigation-without-resolve -->
	<a
		href={trial.url}
		target="_blank"
		rel="noopener noreferrer"
		class="mt-4 inline-block text-sm text-primary-600 no-underline hover:text-primary-700"
		>View on ClinicalTrials.gov ↗</a
	>
	<!-- eslint-enable svelte/no-navigation-without-resolve -->
</div>
