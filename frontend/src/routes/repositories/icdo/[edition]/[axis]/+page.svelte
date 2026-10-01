<script lang="ts">
	import { resolve } from '$app/paths';
	import { icdoCodeSegment } from '$lib/api';
	import DataTable from '$lib/components/data-table/DataTable.svelte';
	import type { DataTableColumn, DataTableOperations } from '$lib/components/data-table/types';
	import RepoBrowsePage from '$lib/components/RepoBrowsePage.svelte';
	import { icdoDatasetKey, parseIcdoDataset } from '$lib/icdo-routes';
	import { columnFilter, gridControls } from '$lib/repository-registry';
	import type { RepositoryPageData } from '$lib/server/repository-load';
	import type { IcdoPage, IcdoRecord, IcdoRepositorySort } from '$lib/types';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
	const dataset = $derived.by(() => {
		const parsed = parseIcdoDataset(data.edition, data.axis);
		if (!parsed) throw new Error('Server returned an unserved ICD-O dataset.');
		return parsed;
	});
	const initial: RepositoryPageData<IcdoPage, IcdoRepositorySort>['initial'] = $derived(data.initial);
	const label = $derived(`ICD-O-${data.edition} ${data.axis}`);
	const route = $derived(resolve('/repositories/icdo/[edition]/[axis]', dataset));
	const controls = $derived(gridControls('icdo', {}, icdoDatasetKey(dataset)));
	const sortKeys = $derived(controls.sortKeys as Record<string, Partial<Record<'asc' | 'desc', IcdoRepositorySort>>>);
</script>

{#snippet codeCell(hit: IcdoRecord)}
	<a class="font-mono text-xs" href={resolve('/repositories/icdo/[edition]/[axis]/[code]', { ...dataset, code: icdoCodeSegment(hit.code) })}>{hit.code}</a>
{/snippet}
{#snippet preferredCell(hit: IcdoRecord)}{hit.preferred ?? 'No preferred term supplied'}{/snippet}
{#snippet levelCell(hit: IcdoRecord)}{hit.level}{/snippet}
{#snippet behaviourCell(hit: IcdoRecord)}{hit.behaviour ?? '—'}{/snippet}
{#snippet specificityCell(hit: IcdoRecord)}{hit.specificity ?? '—'}{/snippet}

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
	{sortKeys}
	filterKeys={controls.filterKeys}
	textKeys={controls.textKeys}
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
		{@const columns = [
			{ id: 'code', label: 'Code', cell: codeCell, sortable: Object.keys(controls.sortKeys.code ?? {}) as ('asc' | 'desc')[], filter: columnFilter('icdo', 'code', 'Filter ICD-O codes', [], icdoDatasetKey(dataset)), sticky: { side: 'left' as const, offset: 0 } },
			{ id: 'preferred', label: 'Preferred/category term', cell: preferredCell, sortable: Object.keys(controls.sortKeys.preferred ?? {}) as ('asc' | 'desc')[], filter: columnFilter('icdo', 'preferred', 'Filter ICD-O preferred terms', [], icdoDatasetKey(dataset)) },
			{ id: 'level', label: 'Level', cell: levelCell, filter: columnFilter('icdo', 'level', 'Filter ICD-O levels', [], icdoDatasetKey(dataset)) },
			{ id: 'behaviour', label: 'Behaviour', cell: behaviourCell, filter: columnFilter('icdo', 'behaviour', 'Filter ICD-O behaviours', [], icdoDatasetKey(dataset)) },
			{ id: 'specificity', label: 'Specificity', cell: specificityCell }
		] satisfies readonly DataTableColumn<IcdoRecord>[]}
		<DataTable rows={hits} {columns} caption={`ICD-O ${dataset.edition} ${dataset.axis} repository records`} regionLabel="ICD-O repository results" getRowId={(hit) => hit.code} {operations} {emptyMessage} stickyHeader={true} />
	{/snippet}
</RepoBrowsePage>
