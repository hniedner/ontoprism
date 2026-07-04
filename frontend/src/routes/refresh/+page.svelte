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
</script>

<h1>Repository refresh</h1>
<p>Re-probe the NCIt and caDSR repositories and report their current version and size.</p>

<button type="button" onclick={run} disabled={loading}>
	{loading ? 'Refreshing…' : 'Refresh repositories'}
</button>

{#if error}
	<p class="error">{error}</p>
{/if}

{#if report}
	<p class="ts">Refreshed at {report.refreshed_at}</p>
	<table>
		<thead>
			<tr><th>Repository</th><th>Status</th><th>Version</th><th>Items</th></tr>
		</thead>
		<tbody>
			{#each report.repositories as repo (repo.name)}
				<tr>
					<td>{repo.name}</td>
					<td class:ok={repo.healthy} class:bad={!repo.healthy}>
						{repo.healthy ? 'healthy' : `error: ${repo.error}`}
					</td>
					<td>{repo.version ?? '—'}</td>
					<td>{repo.item_count?.toLocaleString() ?? '—'}</td>
				</tr>
			{/each}
		</tbody>
	</table>
{/if}

<style>
	button {
		padding: 0.5rem 1rem;
		border: none;
		border-radius: 6px;
		background: #2563eb;
		color: #fff;
		cursor: pointer;
		margin: 1rem 0;
	}
	button:disabled {
		opacity: 0.6;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.9rem;
	}
	th,
	td {
		text-align: left;
		padding: 0.4rem 0.6rem;
		border-bottom: 1px solid #e2e2e2;
	}
	.ok {
		color: #166534;
	}
	.bad {
		color: #b91c1c;
	}
	.ts {
		color: #666;
		font-size: 0.85rem;
	}
	.error {
		color: #b91c1c;
	}
</style>
