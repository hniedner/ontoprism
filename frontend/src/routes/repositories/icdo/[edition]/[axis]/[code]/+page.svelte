<script lang="ts">
	import { resolve } from '$app/paths';
	import AlignmentLinks from '$lib/components/AlignmentLinks.svelte';
	import RepoPageHeader from '$lib/components/RepoPageHeader.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
</script>

<svelte:head><title>{data.record.code} | ICD-O-{data.edition}</title></svelte:head>

<a
	href={resolve('/repositories/icdo/[edition]/[axis]', { edition: data.edition, axis: data.axis })}
	class="mb-4 inline-flex items-center gap-1.5 text-sm text-muted"
>
	<span aria-hidden="true">←</span> Back to {data.edition} {data.axis}
</a>

<RepoPageHeader
	title={data.record.code}
	description={data.record.preferred ?? 'No preferred term supplied by publisher'}
	kind="local-certified-proxy"
/>

<div class="grid gap-6 md:grid-cols-2">
	{#each [
		['Synonyms', data.record.synonyms],
		['Related terms', data.record.related],
		['Notes', data.record.notes],
		['Code references', data.record.code_references],
		['See also', data.record.see_also],
		['See notes', data.record.see_notes],
		['Includes', data.record.includes],
		['Excludes', data.record.excludes],
		['Other publisher text', data.record.other_text]
	] as [heading, values] (heading)}
		{#if values.length}
			<section class="rounded-xl border border-default bg-card p-4 shadow-sm">
				<h2 class="mb-3 text-sm font-semibold text-default">{heading}</h2>
				<ul class="space-y-1 text-sm text-secondary">
					{#each values as value (value)}<li>{value}</li>{/each}
				</ul>
			</section>
		{/if}
	{/each}
</div>

<section class="mt-6">
	<AlignmentLinks title="NCIt concepts asserting this code" alignments={data.alignments} />
</section>
