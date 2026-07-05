<script lang="ts">
	import { runSparql } from '$lib/api';
	import { flattenSparql, type SparqlTable } from '$lib/sparql';

	const NCIT = 'http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#';
	const EXAMPLES = [
		{
			label: 'Roles of Neoplasm (C3262)',
			q: `PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl: <http://www.w3.org/2002/07/owl#>
PREFIX ncit: <${NCIT}>
SELECT ?rel ?target WHERE {
  ncit:C3262 rdfs:subClassOf ?r .
  ?r a owl:Restriction ; owl:onProperty ?rel ; owl:someValuesFrom ?target .
} LIMIT 25`
		},
		{
			label: 'Count owl:Class',
			q: `PREFIX owl: <http://www.w3.org/2002/07/owl#>
SELECT (COUNT(?c) AS ?classes) WHERE { ?c a owl:Class }`
		}
	];

	let query = $state(EXAMPLES[0].q);
	let table = $state<SparqlTable | null>(null);
	let truncated = $state(false);
	let loading = $state(false);
	let error = $state<string | null>(null);

	async function run() {
		const q = query.trim();
		if (!q) return;
		loading = true;
		error = null;
		try {
			const resp = await runSparql(q);
			table = flattenSparql(resp.result);
			truncated = resp.truncated;
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
			table = null;
		} finally {
			loading = false;
		}
	}

	function useExample(q: string) {
		query = q;
		run();
	}
</script>

<svelte:head>
	<title>SPARQL Query · ONTOPRISM</title>
</svelte:head>

<section class="mb-6">
	<h1 class="text-2xl font-semibold text-default">SPARQL Query</h1>
	<p class="mt-1 max-w-2xl text-sm text-muted">
		Run a read-only query against the NCIt store. Update/management forms are rejected and
		results are row-capped.
	</p>
</section>

<form
	class="rounded-xl border border-default bg-card p-4 shadow-sm"
	onsubmit={(e) => {
		e.preventDefault();
		run();
	}}
>
	<textarea
		bind:value={query}
		rows="9"
		spellcheck="false"
		class="w-full resize-y rounded-lg border border-default bg-subtle p-3 font-mono text-xs text-default focus:border-primary-500 focus:outline-none"
		aria-label="SPARQL query"
	></textarea>
	<div class="mt-3 flex flex-wrap items-center gap-2">
		<button
			type="submit"
			disabled={loading}
			class="rounded-lg bg-primary-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-60"
		>
			{loading ? 'Running…' : 'Run query'}
		</button>
		<span class="text-xs text-muted">Examples:</span>
		{#each EXAMPLES as ex (ex.label)}
			<button
				type="button"
				onclick={() => useExample(ex.q)}
				class="rounded-full border border-default px-2.5 py-1 text-xs text-secondary hover:bg-subtle"
			>
				{ex.label}
			</button>
		{/each}
	</div>
</form>

{#if error}
	<div
		class="mt-4 rounded-xl border border-danger-200 bg-danger-50 p-4 text-sm text-danger dark:border-danger-800 dark:bg-danger-900/20"
	>
		{error}
	</div>
{:else if table}
	<div class="mt-4">
		<div class="mb-2 flex items-center gap-2 text-xs text-muted">
			<span>{table.rows.length} row{table.rows.length === 1 ? '' : 's'}</span>
			{#if truncated}
				<span
					class="rounded bg-warning-100 px-2 py-0.5 text-warning-800 dark:bg-warning-900/30 dark:text-warning-200"
					>truncated — row cap reached</span
				>
			{/if}
		</div>
		{#if table.rows.length === 0}
			<p class="text-sm text-muted">No results.</p>
		{:else}
			<div class="overflow-x-auto rounded-xl border border-default">
				<table class="w-full text-left text-xs">
					<thead class="bg-subtle text-secondary">
						<tr>
							{#each table.columns as col (col)}
								<th class="px-3 py-2 font-medium">{col}</th>
							{/each}
						</tr>
					</thead>
					<tbody>
						{#each table.rows as row, i (i)}
							<tr class="border-t border-default">
								{#each row as cell, j (j)}
									<td class="px-3 py-1.5 font-mono text-default">{cell}</td>
								{/each}
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	</div>
{/if}
