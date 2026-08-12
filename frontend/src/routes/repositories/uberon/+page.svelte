<script lang="ts">
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { SvelteURLSearchParams } from 'svelte/reactivity';
	import RepoBrowsePage from '$lib/components/RepoBrowsePage.svelte';
	import type { PageProps } from './$types';
	import type { UberonSearchHit, UberonSource } from '$lib/types';

	let { data }: PageProps = $props();
	const suggestions = ['lung', 'blood vessel', 'epithelial cell', 'neuron'];

	async function setSource(source: UberonSource | null) {
		const params = new SvelteURLSearchParams(page.url.searchParams);
		if (source) params.set('source', source);
		else params.delete('source');
		params.delete('offset');
		const search: '' | `?${string}` = params.size ? `?${params}` : '';
		await goto(resolve(`/repositories/uberon${search}`));
	}
</script>

<RepoBrowsePage
	title="Uberon/CL Concepts"
	route="/repositories/uberon"
	description="Browse the certified combined Uberon and Cell Ontology index, including named hierarchy and OWL restriction relations."
	placeholder="Search Uberon/CL concepts… e.g. lung"
	ariaLabel="Search Uberon and Cell Ontology"
	{suggestions}
	browseTitle="Browsing all Uberon/CL concepts"
	initial={data.initial}
	countLabel={(count, mode) => `${count.toLocaleString()} ${mode === 'search' ? 'matches' : 'concepts'}`}
>
	{#snippet filters()}
		<div class="mb-4 flex items-center gap-2">
			<label for="uberon-source" class="text-sm font-medium text-secondary">Source</label>
			<select
				id="uberon-source"
				class="rounded-lg border border-default bg-card px-3 py-1.5 text-sm text-default"
				value={data.initial.source ?? ''}
				onchange={(event) => setSource((event.currentTarget.value || null) as UberonSource | null)}
			>
				<option value="">Uberon and Cell Ontology</option>
				<option value="uberon">Uberon</option>
				<option value="cl">Cell Ontology</option>
			</select>
		</div>
	{/snippet}
	{#snippet helpText()}
		Search labels and exact synonyms. Each result identifies whether the class is in Uberon or Cell
		Ontology within the certified combined index.
	{/snippet}
	{#snippet results(hits: UberonSearchHit[])}
		<div class="overflow-x-auto">
			<table class="w-full border-collapse text-sm">
				<thead><tr class="border-b border-default"><th class="px-4 py-2 text-left">Code</th><th class="px-4 py-2 text-left">Name</th><th class="px-4 py-2 text-left">Source</th></tr></thead>
				<tbody>
					{#each hits as hit (hit.code)}
						<tr class="border-b border-default/60">
							<td class="px-4 py-2 font-mono text-xs"><a href={resolve('/repositories/uberon/[curie]', { curie: hit.code })}>{hit.code}</a></td>
							<td class="px-4 py-2"><a href={resolve('/repositories/uberon/[curie]', { curie: hit.code })}>{hit.label ?? '—'}</a></td>
							<td class="px-4 py-2">{hit.source === 'cl' ? 'Cell Ontology' : 'Uberon'}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/snippet}
</RepoBrowsePage>
