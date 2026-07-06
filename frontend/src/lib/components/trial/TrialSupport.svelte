<script lang="ts">
	import type { CTStudyDetail } from '$lib/types';

	let { trial }: { trial: CTStudyDetail } = $props();

	const sectionTitle = 'mb-2 text-xs font-semibold uppercase tracking-wide text-muted';
	const card = 'rounded-xl border border-default bg-card p-5 shadow-sm';
</script>

{#if trial.sponsors.length}
	<div class={card}>
		<h2 class={sectionTitle}>Sponsors</h2>
		<ul class="text-sm">
			{#each trial.sponsors as s (s.name)}
				<li class="text-default">
					{s.name}{#if s.role}<span class="ml-1 text-xs text-subtle">{s.role}</span>{/if}
				</li>
			{/each}
		</ul>
	</div>
{/if}

{#if trial.references.length}
	<div class={card}>
		<h2 class={sectionTitle}>References</h2>
		<ul class="space-y-1 text-sm">
			{#each trial.references as ref (ref.citation)}
				<li class="text-default">
					{ref.citation}
					{#if ref.pmid}<span class="ml-1 font-mono text-xs text-subtle">PMID {ref.pmid}</span>{/if}
				</li>
			{/each}
		</ul>
	</div>
{/if}
