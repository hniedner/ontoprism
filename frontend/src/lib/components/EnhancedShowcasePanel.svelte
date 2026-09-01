<script lang="ts">
	import { getEnhancedNcitShowcase } from '$lib/api';
	import { handleLatest } from '$lib/latest';
	import type { EnhancedNcitShowcaseView, ShowcaseDecision } from '$lib/types';

	let { code }: { code: string } = $props();
	let data = $state<EnhancedNcitShowcaseView | null>(null);
	let loaded = $state(false);
	let unavailable = $state(false);

	$effect(() => {
		data = null;
		loaded = false;
		unavailable = false;
		const controller = new AbortController();
		return handleLatest(
			getEnhancedNcitShowcase(code, undefined, controller.signal),
			{
				ready: (result) => (data = result),
				failed: () => (unavailable = true),
				settled: () => (loaded = true)
			},
			() => controller.abort()
		);
	});

	const sections = $derived.by(() => {
		const decisions = data?.decisions ?? [];
		return [
			{ title: 'Active', rows: decisions.filter((row) => row.disposition === 'include') },
			{ title: 'Excluded', rows: decisions.filter((row) => row.disposition === 'exclude') },
			{ title: 'Unresolved', rows: decisions.filter((row) => row.disposition === 'unresolved-visible') }
		];
	});

	function key(row: ShowcaseDecision): string {
		return `${row.axis} ${row.filler}`;
	}
</script>

{#if loaded && unavailable}
	<section class="rounded-xl border border-red-300 bg-red-50 p-4 shadow-sm md:col-span-2 lg:col-span-3">
		<h3 class="text-sm font-semibold text-default">Enhanced NCIt showcase unavailable</h3>
		<p class="mt-2 text-sm text-default">The isolated active showcase could not be loaded. Ordinary NCIt content is unchanged.</p>
	</section>
{:else if loaded && data}
	<section class="rounded-xl border border-amber-300 bg-amber-50 p-4 shadow-sm md:col-span-2 lg:col-span-3">
		<h3 class="text-sm font-semibold text-default">Enhanced NCIt showcase</h3>
		<p class="mt-2 rounded border border-amber-400 bg-white p-2 text-sm font-medium text-default">{data.banner}</p>
		<div class="mt-4 grid gap-4 lg:grid-cols-3">
			{#each sections as section (section.title)}
				<div>
					<h4 class="font-semibold text-default">{section.title}</h4>
					<ul class="mt-2 space-y-3">
						{#each section.rows as row (row.candidate_id)}
							<li class="rounded border border-default bg-card p-3 text-sm">
								<div class="font-medium">{row.label} <span class="text-subtle">({key(row)})</span></div>
								<div class="mt-1 flex flex-wrap gap-1">
									<span class="rounded bg-surface px-1.5 py-0.5">{row.authority}</span>
									{#each row.support as support (support)}<span class="rounded bg-surface px-1.5 py-0.5">{support}</span>{/each}
								</div>
								<p class="mt-2">{row.rationale}</p>
								<p class="mt-1 text-subtle"><strong>Limitations:</strong> {row.limitations}</p>
							</li>
						{/each}
					</ul>
				</div>
			{/each}
		</div>
	</section>
{/if}
