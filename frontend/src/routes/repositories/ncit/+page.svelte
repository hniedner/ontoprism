<script lang="ts">
	import type { SearchHit } from '$lib/types';
	import RepoBrowsePage from '$lib/components/RepoBrowsePage.svelte';
	import SearchResultsTable from '$lib/components/SearchResultsTable.svelte';
	import type { PageProps } from './$types';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { updateRepresentationStatusSearch } from '$lib/representation-status';
	import type { RepresentationStatus } from '$lib/types';

	const SUGGESTIONS = ['melanoma', 'thyroid carcinoma', 'BRCA1 gene', 'tumor stage', 'lung neoplasm'];
	let { data }: PageProps = $props();

	async function setRepresentationStatus(status: RepresentationStatus | null) {
		const params = updateRepresentationStatusSearch(page.url.searchParams, status);
		const search: '' | `?${string}` = params.size ? `?${params}` : '';
		await goto(resolve(`/repositories/ncit${search}`));
	}
</script>

<RepoBrowsePage
	title="NCIt Concepts"
	route="/repositories/ncit"
	description="Browse and search NCI Thesaurus concepts. Explore the biomedical ontology hierarchy, concept roles, and semantically similar terms."
	placeholder="Search NCIt concepts… e.g. breast cancer subtypes"
	ariaLabel="Search NCIt"
	suggestions={SUGGESTIONS}
	browseTitle="Browsing all concepts"
	initial={data.initial}
	countLabel={(n: number, mode: 'browse' | 'search') =>
		`${n.toLocaleString()} ${mode === 'search' ? 'matches' : 'concepts'}`}
>
	{#snippet filters()}
		<div class="mb-4 flex items-center gap-2">
			<label for="representation-status" class="text-sm font-medium text-secondary">
				Representation status
			</label>
			<select
				id="representation-status"
				class="rounded-lg border border-default bg-card px-3 py-1.5 text-sm text-default"
				value={data.initial.representationStatus ?? ''}
				onchange={(event) =>
					setRepresentationStatus(
						event.currentTarget.value === 'legacy-precoordinated'
							? 'legacy-precoordinated'
							: null
					)}
			>
				<option value="">All assessed and unassessed concepts</option>
				<option value="legacy-precoordinated">Legacy pre-coordinated only</option>
			</select>
		</div>
	{/snippet}
	{#snippet helpText()}
		Search by term or synonym (e.g. <em>melanoma</em>). Click any concept to see its definition,
		hierarchy, typed roles, neighborhood graph, mapped caDSR CDEs, and embedding-based similar
		concepts.
	{/snippet}
	{#snippet results(hits: SearchHit[])}
		<SearchResultsTable {hits} />
	{/snippet}
</RepoBrowsePage>
