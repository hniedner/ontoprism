<script lang="ts">
	import type { CTStudyDetail } from '$lib/types';

	let { trial }: { trial: CTStudyDetail } = $props();

	const sectionTitle = 'mb-2 text-xs font-semibold uppercase tracking-wide text-muted';
	const card = 'rounded-xl border border-default bg-card p-5 shadow-sm';
</script>

{#if trial.conditions.length}
	<div class={card}>
		<h2 class={sectionTitle}>Conditions</h2>
		<div class="flex flex-wrap gap-1.5">
			{#each trial.conditions as c (c)}
				<span class="rounded-full bg-subtle px-2.5 py-0.5 text-xs text-secondary">{c}</span>
			{/each}
		</div>
	</div>
{/if}

{#if trial.interventions.length}
	<div class={card}>
		<h2 class={sectionTitle}>Interventions</h2>
		<ul class="space-y-1.5 text-sm">
			{#each trial.interventions as iv (iv.name)}
				<li>
					<span class="font-medium text-default">{iv.name}</span>
					{#if iv.type}<span class="ml-1 text-xs text-subtle">{iv.type}</span>{/if}
					{#if iv.description}<p class="text-muted">{iv.description}</p>{/if}
				</li>
			{/each}
		</ul>
	</div>
{/if}
