<script lang="ts">
	import { resolve } from '$app/paths';
	import type { CTStudySummary } from '$lib/types';

	let { studies }: { studies: CTStudySummary[] } = $props();

	const th = 'px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-muted';
</script>

<div class="overflow-x-auto">
	<table class="w-full border-collapse text-sm">
		<thead>
			<tr class="border-b border-default">
				<th class={th}>NCT ID</th>
				<th class={th}>Title</th>
				<th class={th}>Status</th>
				<th class={th}>Phase</th>
			</tr>
		</thead>
		<tbody>
			{#each studies as trial (trial.nct_id)}
				<tr class="border-b border-default/60 transition-colors hover:bg-subtle">
					<td class="px-4 py-2.5 align-top">
						<a
							href={resolve('/repositories/clinicaltrials/[nct]', { nct: trial.nct_id })}
							class="rounded bg-subtle px-1.5 py-0.5 font-mono text-xs text-primary-600 no-underline hover:text-primary-700 dark:text-primary-400"
						>
							{trial.nct_id}
						</a>
					</td>
					<td class="px-4 py-2.5 align-top">
						<a
							href={resolve('/repositories/clinicaltrials/[nct]', { nct: trial.nct_id })}
							class="font-medium text-default no-underline hover:text-primary-600">{trial.title}</a
						>
						{#if trial.conditions.length}
							<span class="ml-1 text-xs text-subtle">{trial.conditions.join(', ')}</span>
						{/if}
					</td>
					<td class="px-4 py-2.5 align-top text-muted">{trial.status ?? '—'}</td>
					<td class="px-4 py-2.5 align-top">
						{#if trial.phase}
							<span
								class="rounded-md bg-info-50 px-2 py-0.5 text-xs font-medium text-info dark:bg-info-900/30"
								>{trial.phase}</span
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
