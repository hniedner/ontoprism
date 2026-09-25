<script lang="ts">
    import type { PublicationProgress as Progress, DecompositionOutcome, DecompositionReviewFlagKind } from '$lib/types';

	let { progress }: { progress: Progress } = $props();
    const outcomes: Record<DecompositionOutcome, string> = {
        decomposed: 'decomposed', residual: 'residual', 'semantic-excluded': 'semantic-excluded',
        'atomic-no-op': 'atomic-no-op', unknown: 'unknown'
    };
    const flags: Record<DecompositionReviewFlagKind, string> = {
        'needs-review': 'needs-review', 'unresolved-r101-loss': 'unresolved-r101-loss', 'mint-filler': 'mint-filler'
    };
</script>

<section class="bg-status-warning rounded-xl border border-warning-200 p-5 shadow-sm dark:border-warning-800">
	<p class="text-status-warning text-sm font-semibold uppercase tracking-wide">{progress.publication_status}</p>
	<h1 class="mt-1 text-2xl font-bold text-default">Enhanced NCIt publication progress</h1>
	<p class="mt-2 rounded border border-warning-300 bg-card p-3 font-medium text-default dark:border-warning-700">
		{progress.publication_notice}
	</p>
	<p class="mt-3 text-sm text-muted">Published run <span class="font-mono">{progress.run_id}</span> · <strong>{progress.total_concepts}</strong> concepts</p>
	<div class="mt-5 grid gap-5 md:grid-cols-2">
		<div>
			<h2 class="font-semibold text-default">Outcomes</h2>
			<dl class="mt-2 space-y-2">
                {#each Object.keys(outcomes) as key (key)}
                    {@const outcome = key as DecompositionOutcome}
					<div class="flex justify-between rounded bg-card px-3 py-2"><dt>{outcome}</dt><dd class="font-semibold">{progress.outcome_counts[outcome]}</dd></div>
				{/each}
			</dl>
		</div>
		<div>
			<h2 class="font-semibold text-default">Review flags</h2>
			<dl class="mt-2 space-y-2">
                {#each Object.keys(flags) as key (key)}
                    {@const flag = key as DecompositionReviewFlagKind}
					<div class="flex justify-between rounded bg-card px-3 py-2"><dt>{flag}</dt><dd class="font-semibold">{progress.review_flag_counts[flag]}</dd></div>
				{/each}
			</dl>
		</div>
	</div>
</section>
