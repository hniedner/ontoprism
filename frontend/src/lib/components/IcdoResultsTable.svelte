<script lang="ts">
	import { resolve } from '$app/paths';
	import { icdoCodeSegment } from '$lib/api';
	import type { IcdoDataset } from '$lib/icdo-routes';
	import type { IcdoRecord } from '$lib/types';

	let { dataset, hits }: { dataset: IcdoDataset; hits: IcdoRecord[] } = $props();
</script>

<div class="overflow-x-auto">
	<table class="w-full border-collapse text-sm text-default">
		<thead>
			<tr class="border-b border-default">
				<th class="px-4 py-2 text-left">Code</th>
				<th class="px-4 py-2 text-left">Preferred/category term</th>
				<th class="px-4 py-2 text-left">Level</th>
			</tr>
		</thead>
		<tbody>
			{#each hits as hit (hit.code)}
				<tr class="border-b border-default/60">
					<td class="px-4 py-2 font-mono text-xs">
						<a href={resolve('/repositories/icdo/[edition]/[axis]/[code]', {
							edition: dataset.edition,
							axis: dataset.axis,
							code: icdoCodeSegment(hit.code)
						})}>{hit.code}</a>
					</td>
					<td class="px-4 py-2">{hit.preferred ?? 'No preferred term supplied'}</td>
					<td class="px-4 py-2">{hit.level}</td>
				</tr>
			{/each}
		</tbody>
	</table>
</div>
