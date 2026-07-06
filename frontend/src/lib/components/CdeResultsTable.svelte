<script lang="ts">
	import { resolve } from '$app/paths';
	import type { CdeSummary } from '$lib/types';

	let { hits }: { hits: CdeSummary[] } = $props();

	const th = 'px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-muted';
</script>

<div class="overflow-x-auto">
	<table class="w-full border-collapse text-sm">
		<thead>
			<tr class="border-b border-default">
				<th class={th}>Public ID</th>
				<th class={th}>Name</th>
				<th class={th}>Context</th>
				<th class={th}>Type</th>
			</tr>
		</thead>
		<tbody>
			{#each hits as cde (cde.public_id + cde.version)}
				<tr class="border-b border-default/60 transition-colors hover:bg-subtle">
					<td class="px-4 py-2.5 align-top">
						<a
							href={resolve('/repositories/cadsr/[id]', { id: cde.public_id })}
							class="rounded bg-subtle px-1.5 py-0.5 font-mono text-xs text-primary-600 no-underline hover:text-primary-700 dark:text-primary-400"
						>
							{cde.public_id}
						</a>
						<span class="ml-1 text-xs text-subtle">v{cde.version}</span>
					</td>
					<td class="px-4 py-2.5 align-top">
						<a
							href={resolve('/repositories/cadsr/[id]', { id: cde.public_id })}
							class="font-medium text-default no-underline hover:text-primary-600">{cde.long_name}</a
						>
						{#if cde.short_name}
							<span class="ml-1 font-mono text-xs text-subtle">{cde.short_name}</span>
						{/if}
					</td>
					<td class="px-4 py-2.5 align-top text-muted">{cde.context ?? '—'}</td>
					<td class="px-4 py-2.5 align-top">
						{#if cde.datatype}
							<span
								class="rounded-md bg-info-50 px-2 py-0.5 text-xs font-medium text-info dark:bg-info-900/30"
								>{cde.datatype}</span
							>
						{:else}
							<span class="text-muted">—</span>
						{/if}
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
</div>
