<script lang="ts">
	import { resolve } from '$app/paths';
	import type { ConceptLink } from '$lib/types';

	let { concepts }: { concepts: ConceptLink[] } = $props();
	const LIST_PREVIEW = 6;
	let expanded = $state(false);
	const visibleConcepts = $derived(expanded ? concepts : concepts.slice(0, LIST_PREVIEW));
</script>

<section class="min-w-0 rounded-xl border border-default bg-card p-4 shadow-sm">
	<h3 class="mb-3 text-sm font-semibold text-default">NCIt concepts ({concepts.length})</h3>
	<ul class="flex flex-col gap-2.5">
		{#each visibleConcepts as concept (concept.concept_code)}
			<li class="flex flex-wrap items-baseline gap-1.5 text-sm">
				<a
					href={resolve('/repositories/ncit/[code]', { code: concept.concept_code })}
					class="text-secondary no-underline hover:text-primary-600">{concept.concept_name}</a
				>
				<span class="font-mono text-xs text-subtle">{concept.concept_code}</span>
				{#if concept.is_primary}<span class="text-xs text-success">primary</span>{/if}
				{#if concept.concept_type}<span class="text-xs text-muted">{concept.concept_type}</span>{/if}
			</li>
		{:else}
			<li class="text-sm italic text-subtle">None.</li>
		{/each}
	</ul>
	{#if concepts.length > LIST_PREVIEW}
		<button
			type="button"
			aria-expanded={expanded}
			class="mt-3 text-xs font-medium text-primary-600 hover:text-primary-700 dark:text-primary-400"
			onclick={() => (expanded = !expanded)}
		>
			{expanded ? 'Show fewer NCIt concepts' : `Show all ${concepts.length} NCIt concepts`}
		</button>
	{/if}
</section>
