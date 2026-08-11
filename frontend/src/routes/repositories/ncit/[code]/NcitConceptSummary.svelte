<script lang="ts">
	import { resolve } from '$app/paths';
	import type { ConceptDetail } from '$lib/types';

	let { detail }: { detail: ConceptDetail } = $props();
</script>

<header class="mb-6">
	<h1 class="text-2xl font-semibold text-default">{detail.label ?? detail.code}</h1>
	<div class="mt-2 flex flex-wrap items-center gap-2">
		<span class="rounded bg-subtle px-2 py-0.5 font-mono text-xs text-secondary">{detail.code}</span>
		{#each detail.semantic_types as semanticType (semanticType)}
			<span
				class="rounded-full bg-primary-50 px-2.5 py-0.5 text-xs font-medium text-primary-700 dark:bg-primary-900/30 dark:text-primary-300"
				>{semanticType}</span
			>
		{/each}
	</div>
	{#if detail.definition}
		<p class="mt-3 max-w-3xl text-sm leading-relaxed text-secondary">{detail.definition}</p>
	{/if}
	{#if detail.synonyms.length}
		<p class="mt-2 max-w-3xl text-sm text-muted">
			<span class="font-medium text-secondary">Synonyms:</span>
			{detail.synonyms.join(', ')}
		</p>
	{/if}
</header>

<div class="mt-6 grid gap-6 md:grid-cols-2">
	{#each [{ title: 'Parents', concepts: detail.parents }, { title: 'Children', concepts: detail.children }] as group (group.title)}
		<section class="rounded-xl border border-default bg-card p-4 shadow-sm">
			<h3 class="mb-3 flex items-center gap-2 text-sm font-semibold text-default">
				{group.title}
				<span class="rounded-full bg-subtle px-2 py-0.5 text-xs font-normal text-muted"
					>{group.concepts.length}</span
				>
			</h3>
			<ul class="flex flex-col gap-2 text-sm">
				{#each group.concepts as concept (concept.code)}
					<li>
						<a
							href={resolve('/repositories/ncit/[code]', { code: concept.code })}
							class="text-secondary no-underline hover:text-primary-600"
							>{concept.label ?? concept.code}</a
						>
					</li>
				{:else}
					<li class="italic text-subtle">None.</li>
				{/each}
			</ul>
		</section>
	{/each}
</div>
