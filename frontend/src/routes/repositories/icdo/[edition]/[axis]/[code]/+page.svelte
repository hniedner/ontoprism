<script lang="ts">import AlignmentLinks from '$lib/components/AlignmentLinks.svelte'; import type { PageProps } from './$types'; let { data }: PageProps = $props();</script>
<svelte:head><title>{data.record.code} | ICD-O-{data.edition}</title></svelte:head>
<article class="mx-auto max-w-4xl px-6 py-8"><p class="text-sm font-semibold uppercase tracking-wider text-primary-700">ICD-O-{data.edition} {data.axis}</p><h1 class="mt-2 font-mono text-3xl font-bold">{data.record.code}</h1><h2 class="mt-3 text-xl">{data.record.preferred ?? 'No preferred term supplied by publisher'}</h2>
	{#if data.record.synonyms.length}<section class="mt-8"><h3 class="font-semibold">Synonyms</h3><ul>{#each data.record.synonyms as term (term)}<li>{term}</li>{/each}</ul></section>{/if}
	{#if data.record.related.length}<section class="mt-8"><h3 class="font-semibold">Related terms</h3><ul>{#each data.record.related as term (term)}<li>{term}</li>{/each}</ul></section>{/if}
	{#each [
		['Notes', data.record.notes],
		['Code references', data.record.code_references],
		['See also', data.record.see_also],
		['See notes', data.record.see_notes],
		['Includes', data.record.includes],
		['Excludes', data.record.excludes],
		['Other publisher text', data.record.other_text]
	] as [heading, values] (heading)}
		{#if values.length}<section class="mt-8"><h3 class="font-semibold">{heading}</h3><ul>{#each values as value (value)}<li>{value}</li>{/each}</ul></section>{/if}
	{/each}
	<section class="mt-8"><AlignmentLinks title="NCIt concepts asserting this code" alignments={data.alignments} /></section>
</article>
