<script lang="ts">
	import type { PublicationProgress as Progress } from '$lib/types';

	let { progress }: { progress: Progress } = $props();
	const outcomes = [
		'decomposed',
		'residual',
		'semantic-excluded',
		'atomic-no-op',
		'unknown'
	] as const;
	const flags = ['needs-review', 'unresolved-r101-loss', 'mint-filler'] as const;
</script>

<section class="rounded-xl border border-amber-300 bg-amber-50 p-5 shadow-sm">
	<p class="text-sm font-semibold uppercase tracking-wide text-amber-900">{progress.publication_status}</p>
	<h1 class="mt-1 text-2xl font-bold text-default">Enhanced NCIt publication progress</h1>
	<p class="mt-2 rounded border border-amber-400 bg-white p-3 font-medium text-default">
		{progress.publication_notice}
	</p>
	<p class="mt-3 text-sm text-muted">Published run <span class="font-mono">{progress.run_id}</span> · <strong>{progress.total_concepts}</strong> concepts</p>
	<div class="mt-5 grid gap-5 md:grid-cols-2">
		<div>
			<h2 class="font-semibold text-default">Outcomes</h2>
			<dl class="mt-2 space-y-2">
				{#each outcomes as outcome (outcome)}
					<div class="flex justify-between rounded bg-card px-3 py-2"><dt>{outcome}</dt><dd class="font-semibold">{progress.outcome_counts[outcome]}</dd></div>
				{/each}
			</dl>
		</div>
		<div>
			<h2 class="font-semibold text-default">Review flags</h2>
			<dl class="mt-2 space-y-2">
				{#each flags as flag (flag)}
					<div class="flex justify-between rounded bg-card px-3 py-2"><dt>{flag}</dt><dd class="font-semibold">{progress.review_flag_counts[flag]}</dd></div>
				{/each}
			</dl>
		</div>
	</div>
</section>
