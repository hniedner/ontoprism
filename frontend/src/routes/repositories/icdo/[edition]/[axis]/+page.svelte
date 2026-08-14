<script lang="ts">
	import { resolve } from '$app/paths';
	import { icdoCodeSegment } from '$lib/api';
	import { SvelteURLSearchParams } from 'svelte/reactivity';
	import type { PageProps } from './$types';
	let { data }: PageProps = $props();
	const label = $derived(`ICD-O-${data.edition} ${data.axis}`);
	const hasPrevious = $derived(data.result.offset > 0);
	const hasNext = $derived(data.result.offset + data.result.limit < data.result.total);

	function pageHref(offset: number): string {
		const params = new SvelteURLSearchParams();
		if (data.query) params.set('q', data.query);
		if (data.behaviour) params.set('behaviour', data.behaviour);
		if (data.level) params.set('level', data.level);
		if (offset > 0) params.set('offset', String(offset));
		const query = params.toString();
		const path = resolve('/repositories/icdo/[edition]/[axis]', {
			edition: data.edition,
			axis: data.axis
		});
		return query ? `${path}?${query}` : path;
	}
</script>

<svelte:head><title>{label} | OntoPrism</title></svelte:head>
<section class="mx-auto max-w-6xl px-6 py-8">
	<p class="text-sm font-semibold uppercase tracking-wider text-primary-700">Certified local repository</p>
	<h1 class="mt-2 text-3xl font-bold">{label}</h1>
	<p class="mt-2 text-secondary">{data.result.total.toLocaleString()} records in the active {data.edition} {data.axis} generation.</p>
	{#if data.edition === '4.0' && data.axis === 'topography'}<p class="mt-3"><a href={resolve('/repositories/icdo/4.0/topography/congruence')}>View Uberon congruence report</a></p>{/if}
	<form class="my-6 flex gap-2" method="GET"><input class="min-w-0 flex-1 rounded-lg border border-default bg-card px-4 py-2" name="q" value={data.query} aria-label={`Search ${label}`} /><button class="rounded-lg bg-primary-700 px-5 py-2 text-white">Search</button></form>
	<div class="overflow-x-auto rounded-xl border border-default bg-card"><table class="w-full text-left text-sm"><thead><tr class="border-b border-default"><th class="p-3">Code</th><th class="p-3">Preferred/category term</th><th class="p-3">Level</th></tr></thead><tbody>
		{#each data.result.hits as hit (hit.code)}<tr class="border-b border-default/60"><td class="p-3 font-mono"><a href={resolve('/repositories/icdo/[edition]/[axis]/[code]', { edition: data.edition, axis: data.axis, code: icdoCodeSegment(hit.code) })}>{hit.code}</a></td><td class="p-3">{hit.preferred ?? 'No preferred term supplied'}</td><td class="p-3">{hit.level}</td></tr>{/each}
	</tbody></table>
		<div class="flex items-center justify-between gap-4 border-t border-default p-3 text-sm">
			<span class="text-muted">Showing {data.result.total === 0 ? 0 : data.result.offset + 1}–{Math.min(data.result.offset + data.result.limit, data.result.total)} of {data.result.total.toLocaleString()}</span>
			<div class="flex gap-2">
				<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- pageHref resolves this typed route and appends preserved URL state -->
				{#if hasPrevious}<a class="rounded-md border border-default px-3 py-1" href={pageHref(Math.max(0, data.result.offset - data.result.limit))}>Previous</a>{/if}
				<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- pageHref resolves this typed route and appends preserved URL state -->
				{#if hasNext}<a class="rounded-md border border-default px-3 py-1" href={pageHref(data.result.offset + data.result.limit)}>Next</a>{/if}
			</div>
		</div>
	</div>
</section>
