<script lang="ts">
	import { refreshRepositories } from '$lib/api';
	import type { RefreshReport } from '$lib/types';

	let report = $state<RefreshReport | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);

	async function run() {
		loading = true;
		error = null;
		try {
			report = await refreshRepositories();
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
		} finally {
			loading = false;
		}
	}

	const th = 'px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-muted';
</script>

<svelte:head>
	<title>Refresh · ONTOPRISM</title>
</svelte:head>

<div class="mb-6">
	<h1 class="text-2xl font-semibold text-default">Repository Refresh</h1>
	<p class="mt-1 max-w-3xl text-sm text-muted">
		Re-probe the NCIt and caDSR repositories and report their current version and size.
	</p>
</div>

<button
	type="button"
	onclick={run}
	disabled={loading}
	class="mb-6 inline-flex items-center gap-2 rounded-lg bg-primary-600 px-5 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-primary-700 disabled:opacity-50"
>
	<svg
		viewBox="0 0 24 24"
		class="h-4 w-4 {loading ? 'animate-spin' : ''}"
		fill="none"
		stroke="currentColor"
		stroke-width="1.8"
	>
		<path d="M21 12a9 9 0 1 1-2.6-6.4M21 4v4h-4" stroke-linecap="round" stroke-linejoin="round" />
	</svg>
	{loading ? 'Refreshing…' : 'Refresh repositories'}
</button>

{#if error}
	<div
		class="mb-6 rounded-xl border border-danger-200 bg-danger-50 p-4 text-sm text-danger dark:border-danger-800 dark:bg-danger-900/20"
	>
		{error}
	</div>
{/if}

{#if report}
	<p class="mb-2 text-xs text-muted">Refreshed at {report.refreshed_at}</p>
	<div class="overflow-hidden rounded-xl border border-default bg-card shadow-sm">
		<div class="overflow-x-auto">
			<table class="w-full border-collapse text-sm">
				<thead>
					<tr class="border-b border-default">
						<th class={th}>Repository</th>
						<th class={th}>Status</th>
						<th class={th}>Version</th>
						<th class={th}>Items</th>
					</tr>
				</thead>
				<tbody>
					{#each report.repositories as repo (repo.name)}
						<tr class="border-b border-default/60">
							<td class="px-4 py-2.5 font-medium text-default">{repo.name}</td>
							<td class="px-4 py-2.5">
								{#if repo.healthy}
									<span
										class="inline-flex items-center gap-1.5 rounded-full bg-success-50 px-2.5 py-0.5 text-xs font-medium text-success dark:bg-success-900/30"
									>
										<span class="h-1.5 w-1.5 rounded-full bg-current"></span> healthy
									</span>
								{:else}
									<span
										class="inline-flex items-center gap-1.5 rounded-full bg-danger-50 px-2.5 py-0.5 text-xs font-medium text-danger dark:bg-danger-900/30"
									>
										<span class="h-1.5 w-1.5 rounded-full bg-current"></span> error: {repo.error}
									</span>
								{/if}
							</td>
							<td class="px-4 py-2.5 text-muted">{repo.version ?? '—'}</td>
							<td class="px-4 py-2.5 text-muted">{repo.item_count?.toLocaleString() ?? '—'}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</div>
{/if}
