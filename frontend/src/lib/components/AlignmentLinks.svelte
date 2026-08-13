<script lang="ts">
	import { resolve } from '$app/paths';
	import { icdoCodeSegment } from '$lib/api';
	import type { Alignment } from '$lib/types';

	let { title, alignments }: { title: string; alignments: Alignment[] } = $props();

	function repositoryName(system: Alignment['system']): string {
		if (system === 'ncit') return 'NCIt';
		if (system === 'icdo') return 'ICD-O-3.2 morphology';
		return 'Uberon/CL';
	}
</script>

<section class="rounded-xl border border-default bg-card p-4 shadow-sm">
	<h2 class="mb-3 text-sm font-semibold text-default">{title}</h2>
	{#if alignments.length === 0}
		<p class="text-sm italic text-subtle">No publisher alignments.</p>
	{:else}
		<ul class="flex flex-col gap-2">
			{#each alignments as alignment (alignment.system + alignment.code)}
				<li class="flex flex-wrap items-baseline gap-2 text-sm">
					{#if alignment.system === 'ncit'}
						<a
							href={resolve('/repositories/ncit/[code]', { code: alignment.code })}
							aria-label={`Open aligned NCIt concept ${alignment.code}`}
							class="font-mono text-primary-700 dark:text-primary-300"
						>{alignment.code}</a>
					{:else if alignment.system === 'icdo'}
						<a
							href={resolve('/repositories/icdo/[edition]/[axis]/[code]', {
								edition: alignment.version,
								axis: 'morphology',
								code: icdoCodeSegment(alignment.code)
							})}
							aria-label={`Open aligned ${repositoryName(alignment.system)} code ${alignment.code}`}
							class="font-mono text-primary-700 dark:text-primary-300"
						>{alignment.code}</a>
					{:else}
						<a
							href={resolve('/repositories/uberon/[curie]', { curie: alignment.code })}
							aria-label={`Open aligned ${repositoryName(alignment.system)} concept ${alignment.code}`}
							class="font-mono text-primary-700 dark:text-primary-300"
						>{alignment.code}</a>
					{/if}
					<span class="text-xs text-muted">Proposed close alignment</span>
				</li>
			{/each}
		</ul>
	{/if}
</section>
