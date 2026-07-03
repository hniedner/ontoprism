<script lang="ts">
	import { searchNcit } from '$lib/api';
	import type { SearchPage } from '$lib/types';
	import SearchResultsTable from '$lib/components/SearchResultsTable.svelte';

	let q = $state('');
	let result = $state<SearchPage | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);

	async function run(event: SubmitEvent) {
		event.preventDefault();
		const term = q.trim();
		if (!term) return;
		loading = true;
		error = null;
		try {
			result = await searchNcit(term, { limit: 50 });
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
			result = null;
		} finally {
			loading = false;
		}
	}
</script>

<h1>NCIt repository</h1>

<form onsubmit={run}>
	<input
		type="search"
		bind:value={q}
		placeholder="Search NCIt — e.g. melanoma, thyroid carcinoma"
		aria-label="Search NCIt"
	/>
	<button type="submit" disabled={loading}>Search</button>
</form>

{#if loading}
	<p>Searching…</p>
{:else if error}
	<p class="error">Search failed: {error}</p>
{:else if result}
	<p class="summary">{result.total.toLocaleString()} result(s) for “{result.query}”</p>
	<SearchResultsTable hits={result.hits} />
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
	button:disabled {
		opacity: 0.6;
	}
	.summary {
		color: #555;
	}
	.error {
		color: #b91c1c;
	}
</style>
