<script lang="ts">
	import { resolve } from '$app/paths';
	import SimilarCdes from '$lib/components/SimilarCdes.svelte';
	import type { CdeDetail } from '$lib/types';

	let { cde }: { cde: CdeDetail } = $props();
</script>

<header class="mb-6">
	<h1 class="text-2xl font-semibold text-default">{cde.long_name}</h1>
	<div class="mt-2 flex flex-wrap items-center gap-2">
		<span class="rounded bg-subtle px-2 py-0.5 font-mono text-xs text-secondary"
			>{cde.public_id} v{cde.version}</span
		>
		{#each [cde.short_name, cde.context, cde.datatype].filter(Boolean) as label (label)}
			<span class="rounded bg-subtle px-2 py-0.5 text-xs text-muted">{label}</span>
		{/each}
	</div>
	{#if cde.definition}
		<p class="mt-3 max-w-3xl text-sm leading-relaxed text-secondary">{cde.definition}</p>
	{/if}
</header>

<div class="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
	<section class="rounded-xl border border-default bg-card p-4 shadow-sm">
		<h3 class="mb-3 text-sm font-semibold text-default">NCIt concepts ({cde.concepts.length})</h3>
		<ul class="flex flex-col gap-2.5">
			{#each cde.concepts as concept (concept.concept_code)}
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
	</section>

	<section class="rounded-xl border border-default bg-card p-4 shadow-sm">
		<h3 class="mb-3 text-sm font-semibold text-default">
			Permissible values ({cde.permissible_values.length})
		</h3>
		<ul class="flex flex-col gap-2 text-sm">
			{#each cde.permissible_values as value (value.value + (value.meaning_code ?? ''))}
				<li>
					<strong class="text-default">{value.value}</strong>
					{#if value.meaning}<span class="text-muted"> — {value.meaning}</span>{/if}
				</li>
			{:else}
				<li class="italic text-subtle">Not an enumerated value domain.</li>
			{/each}
		</ul>
	</section>

	<SimilarCdes publicId={cde.public_id} />
</div>
