<script lang="ts">
	import { resolve } from '$app/paths';
	import type { SearchHit } from '$lib/types';
	import { nextDir, sortBy, type SortDir } from '$lib/sort';

	let { hits }: { hits: SearchHit[] } = $props();

	type Col = 'code' | 'label' | 'semantic_type';
	let sortKey = $state<Col>('label');
	let sortDir = $state<SortDir>('asc');

	const value = (h: SearchHit, k: Col): string | null =>
		k === 'code' ? h.code : k === 'label' ? h.label : h.semantic_type;

	let sorted = $derived(sortBy(hits, (h) => value(h, sortKey), sortDir));

	function toggle(k: Col) {
		if (sortKey === k) sortDir = nextDir(sortDir);
		else {
			sortKey = k;
			sortDir = 'asc';
		}
	}

	const arrow = (k: Col) => (sortKey === k ? (sortDir === 'asc' ? '↑' : '↓') : '↕');

	const th =
		'px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-muted select-none';
</script>

<div class="overflow-x-auto">
	<table class="w-full border-collapse text-sm">
		<thead>
			<tr class="border-b border-default">
				<th class={th}>
					<button
						type="button"
						class="inline-flex items-center gap-1 hover:text-default"
						onclick={() => toggle('code')}
					>
						Code <span class="text-subtle">{arrow('code')}</span>
					</button>
				</th>
				<th class={th}>
					<button
						type="button"
						class="inline-flex items-center gap-1 hover:text-default"
						onclick={() => toggle('label')}
					>
						Name <span class="text-subtle">{arrow('label')}</span>
					</button>
				</th>
				<th class={th}>
					<button
						type="button"
						class="inline-flex items-center gap-1 hover:text-default"
						onclick={() => toggle('semantic_type')}
					>
						Semantic type <span class="text-subtle">{arrow('semantic_type')}</span>
					</button>
				</th>
			</tr>
		</thead>
		<tbody>
			{#each sorted as hit (hit.code)}
				<tr class="border-b border-default/60 transition-colors hover:bg-subtle">
					<td class="px-4 py-2.5 align-top">
						<a
							href={resolve('/repositories/ncit/[code]', { code: hit.code })}
							class="rounded bg-subtle px-1.5 py-0.5 font-mono text-xs text-primary-600 no-underline hover:text-primary-700 dark:text-primary-400"
						>
							{hit.code}
						</a>
					</td>
					<td class="px-4 py-2.5 align-top font-medium text-default">
						<a
							href={resolve('/repositories/ncit/[code]', { code: hit.code })}
							class="no-underline hover:text-primary-600"
						>
							{hit.label ?? '—'}
						</a>
					</td>
					<td class="px-4 py-2.5 align-top text-muted">
						{#if hit.semantic_type}
							<span class="whitespace-nowrap">{hit.semantic_type}</span>
						{:else}
							—
						{/if}
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
</div>

{#if sorted.length === 0}
	<p class="px-4 py-6 text-center text-sm italic text-muted">No results.</p>
{/if}
