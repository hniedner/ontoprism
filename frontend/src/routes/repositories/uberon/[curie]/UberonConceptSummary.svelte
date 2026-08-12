<script lang="ts">
	import type { UberonConceptDetail } from '$lib/types';

	let { detail }: { detail: UberonConceptDetail } = $props();
</script>

<section class="rounded-xl border border-default bg-card p-5 shadow-sm">
	<div class="mb-2 flex items-center gap-2">
		<code>{detail.code}</code>
		<span class="rounded-full bg-subtle px-2 py-0.5 text-xs">
			{detail.source === 'cl' ? 'Cell Ontology' : 'Uberon'}
		</span>
	</div>
	<h1 class="text-2xl font-semibold text-default">{detail.label ?? detail.code}</h1>
	{#if detail.definition}<p class="mt-3 text-secondary">{detail.definition}</p>{/if}
	{#if detail.synonyms.length}
		<p class="mt-3 text-sm text-muted">Synonyms: {detail.synonyms.join('; ')}</p>
	{/if}
	{#if detail.xrefs.length}
		<p class="mt-3 text-sm text-muted">Cross-references: {detail.xrefs.join('; ')}</p>
	{/if}
	{#if detail.truncated}
		<p role="status" class="mt-3 text-sm text-warning">
			This concept has additional relationships beyond the displayed detail limit.
		</p>
	{/if}
</section>
