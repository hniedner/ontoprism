<script lang="ts">
	import { resolve } from '$app/paths';
	import IcdoResultsTable from '$lib/components/IcdoResultsTable.svelte';
	import RepoBrowsePage from '$lib/components/RepoBrowsePage.svelte';
	import { parseIcdoDataset } from '$lib/icdo-routes';
	import type { RepositoryPageData } from '$lib/server/repository-load';
	import type { IcdoPage, IcdoRecord, IcdoRepositorySort } from '$lib/types';
	import type { PageProps } from './$types';
	import type { DataTableOperations } from '$lib/components/data-table/types';

	let { data }: PageProps = $props();
	const dataset = $derived.by(() => {
		const parsed = parseIcdoDataset(data.edition, data.axis);
		if (!parsed) throw new Error('Server returned an unserved ICD-O dataset.');
		return parsed;
	});
	const initial: RepositoryPageData<IcdoPage, IcdoRepositorySort>['initial'] = $derived(data.initial);
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
	{initial}
	defaultSort="source"
	sortKeys={{ code: { asc: 'code:asc', desc: 'code:desc' }, preferred: { asc: 'preferred:asc', desc: 'preferred:desc' } }}
	filterKeys={dataset.axis === 'morphology' ? { level: 'level', behaviour: 'behaviour' } : { level: 'level' }}
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
	{#snippet results(hits: IcdoRecord[], operations: DataTableOperations, emptyMessage: string)}
		<IcdoResultsTable {dataset} {hits} {operations} {emptyMessage} />
	{/snippet}
</RepoBrowsePage>
