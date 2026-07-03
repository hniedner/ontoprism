<script lang="ts">
	import { resolve } from '$app/paths';
	import { searchCadsr } from '$lib/api';
	import type { CdeSearchPage } from '$lib/types';

	let q = $state('');
	let result = $state<CdeSearchPage | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);

	async function run(event: SubmitEvent) {
		event.preventDefault();
		const term = q.trim();
		if (!term) return;
		loading = true;
		error = null;
		try {
			result = await searchCadsr(term, { limit: 50 });
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
			result = null;
		} finally {
			loading = false;
		}
	}
</script>

<h1>caDSR repository</h1>

<form onsubmit={run}>
	<input type="search" bind:value={q} placeholder="Search CDEs — e.g. stage, age" aria-label="Search caDSR" />
	<button type="submit" disabled={loading}>Search</button>
</form>

{#if loading}
	<p>Searching…</p>
{:else if error}
	<p class="error">Search failed: {error}</p>
{:else if result}
	<p class="summary">{result.total.toLocaleString()} CDE(s) for “{result.query}”</p>
	<table>
		<thead>
			<tr><th>Public ID</th><th>Name</th><th>Context</th><th>Type</th></tr>
		</thead>
		<tbody>
			{#each result.hits as cde (cde.public_id + cde.version)}
				<tr>
					<td><a href={resolve('/repositories/cadsr/[id]', { id: cde.public_id })}>{cde.public_id}</a></td>
					<td>{cde.long_name}</td>
					<td>{cde.context ?? '—'}</td>
					<td>{cde.datatype ?? '—'}</td>
				</tr>
			{/each}
		</tbody>
	</table>
{/if}

<style>
	form {
		display: flex;
		gap: 0.5rem;
		margin: 1rem 0;
	}
	input {
		flex: 1;
		padding: 0.5rem 0.7rem;
		font-size: 1rem;
		border: 1px solid #cbd5e1;
		border-radius: 6px;
	}
	button {
		padding: 0.5rem 1rem;
		border: none;
		border-radius: 6px;
		background: #2563eb;
		color: #fff;
		cursor: pointer;
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
	tbody tr:hover {
		background: #f6f8fa;
	}
	.error {
		color: #b91c1c;
	}
	.summary {
		color: #555;
	}
</style>
