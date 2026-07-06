<script lang="ts">
	import type { CTStudyDetail } from '$lib/types';

	let { trial }: { trial: CTStudyDetail } = $props();

	const sectionTitle = 'mb-2 text-xs font-semibold uppercase tracking-wide text-muted';
	const card = 'rounded-xl border border-default bg-card p-5 shadow-sm';
</script>

{#if trial.primary_outcomes.length || trial.secondary_outcomes.length}
	<div class={card}>
		<h2 class={sectionTitle}>Outcomes</h2>
		<ul class="space-y-1.5 text-sm">
			{#each trial.primary_outcomes as o (o.measure)}
				<li>
					<span class="font-medium text-default">{o.measure}</span>
					<span class="ml-1 text-xs text-primary-600">primary</span>
					{#if o.time_frame}<span class="ml-1 text-xs text-subtle">· {o.time_frame}</span>{/if}
				</li>
			{/each}
			{#each trial.secondary_outcomes as o (o.measure)}
				<li>
					<span class="font-medium text-default">{o.measure}</span>
					<span class="ml-1 text-xs text-muted">secondary</span>
				</li>
			{/each}
		</ul>
	</div>
{/if}

{#if trial.eligibility_criteria}
	<div class={card}>
		<h2 class={sectionTitle}>Eligibility</h2>
		<p class="whitespace-pre-line text-sm text-default">{trial.eligibility_criteria}</p>
	</div>
{/if}
