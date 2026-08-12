<script lang="ts">
	import { refreshRepositories } from '$lib/api';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import RepositoryMetadataRow from '$lib/components/RepositoryMetadataRow.svelte';
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
		Re-certify the active NCIt, caDSR, and Uberon/CL local proxies and report their manifest-bound identities.
		Remote live services are not refreshed.
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
		class="h-4 w-4"
		fill="none"
		stroke="currentColor"
		stroke-width="1.8"
	>
		<path d="M21 12a9 9 0 1 1-2.6-6.4M21 4v4h-4" stroke-linecap="round" stroke-linejoin="round" />
	</svg>
	{loading ? 'Refreshing…' : 'Refresh repositories'}
</button>


<div aria-live="polite" class="min-h-16">
	<LoadingState active={loading} label="Refreshing repositories" minHeight="4rem" />
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
							<th class={th}>Release / Items</th>
							<th class={th}>Source identity</th>
							<th class={th}>Manifest identity</th>
						</tr>
					</thead>
					<tbody>
						{#each report.repositories as repo (`${repo.repository}:${repo.state}`)}
							<RepositoryMetadataRow repository={repo} />
						{/each}
					</tbody>
				</table>
			</div>
		</div>
	{/if}
</div>
