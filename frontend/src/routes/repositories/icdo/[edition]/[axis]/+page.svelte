<script lang="ts">
	import { resolve } from '$app/paths';
	import IcdoResultsTable from '$lib/components/IcdoResultsTable.svelte';
	import RepoBrowsePage from '$lib/components/RepoBrowsePage.svelte';
	import type { IcdoDataset } from '$lib/icdo-routes';
	import type { IcdoRecord } from '$lib/types';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
	const dataset = $derived({ edition: data.edition, axis: data.axis } as IcdoDataset);
	const label = $derived(`ICD-O-${data.edition} ${data.axis}`);
	const route = $derived(resolve('/repositories/icdo/[edition]/[axis]', dataset));
</script>

<RepoBrowsePage
	title={label}
	{route}
	description={`Browse the certified ICD-O-${data.edition} ${data.axis} active generation.`}
	placeholder={`Search ${label} codes and terms…`}
	ariaLabel={`Search ${label}`}
	suggestions={[]}
	browseTitle={`Browsing ${label} records`}
	initial={{ result: data.result, query: data.query, offset: data.result.offset }}
	countLabel={(count, mode) => `${count.toLocaleString()} ${mode === 'search' ? 'matches' : 'records'}`}
>
	{#snippet filters()}
		{#if data.edition === '4.0' && data.axis === 'topography'}
			<p class="mb-4 text-sm"><a href={resolve('/repositories/icdo/4.0/topography/congruence')}>View Uberon congruence report</a></p>
		{/if}
	{/snippet}
	{#snippet helpText()}
		Search publisher codes, preferred terms, synonyms, and related terms in this certified
		edition/axis dataset.
	{/snippet}
	{#snippet results(hits: IcdoRecord[])}
		<IcdoResultsTable {dataset} {hits} />
	{/snippet}
</RepoBrowsePage>
