<script lang="ts">
	import type { RepositoryMetadata } from '$lib/types';

	let { repository }: { repository: RepositoryMetadata } = $props();
</script>

<tr class="border-b border-default/60">
	<td class="px-4 py-2.5 font-medium text-default">{repository.repository}</td>
	<td class="px-4 py-2.5">
		{#if repository.state === 'ready'}
			<span
				class="inline-flex items-center gap-1.5 rounded-full bg-success-50 px-2.5 py-0.5 text-xs font-medium text-success dark:bg-success-900/30"
			>
				<span class="h-1.5 w-1.5 rounded-full bg-current"></span> ready
			</span>
		{:else}
			<div class="text-danger">
				<span
					class="inline-flex items-center gap-1.5 rounded-full bg-danger-50 px-2.5 py-0.5 text-xs font-medium dark:bg-danger-900/30"
				>
					<span class="h-1.5 w-1.5 rounded-full bg-current"></span> {repository.reason}
				</span>
				<p class="mt-1 max-w-sm text-xs">{repository.message}</p>
			</div>
		{/if}
	</td>
	{#if repository.state === 'ready'}
		<td class="px-4 py-2.5 text-muted">
			{repository.repository === 'ncit'
				? repository.release
				: repository.repository === 'cadsr'
					? repository.item_count.toLocaleString()
					: repository.repository === 'uberon'
						? (repository.class_counts.uberon + repository.class_counts.cl).toLocaleString()
						: `${repository.edition} ${repository.axis} (${repository.row_count.toLocaleString()})`}
		</td>
		<td class="max-w-64 break-all px-4 py-2.5 font-mono text-xs text-muted">
			{repository.source_identity}
		</td>
		<td class="max-w-64 break-all px-4 py-2.5 font-mono text-xs text-muted">
			{'manifest_identity' in repository
				? repository.manifest_identity
				: repository.activation_identity}
		</td>
	{:else}
		<td class="px-4 py-2.5 text-muted">—</td>
		<td class="px-4 py-2.5 text-muted">—</td>
		<td class="px-4 py-2.5 text-muted">—</td>
	{/if}
</tr>
