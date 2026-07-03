<script lang="ts">
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import { getCde } from '$lib/api';
	import type { CdeDetail } from '$lib/types';

	let cde = $state<CdeDetail | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);

	$effect(() => {
		const id = page.params.id;
		if (!id) return;
		loading = true;
		error = null;
		cde = null;
		getCde(id)
			.then((c) => (cde = c))
			.catch((err) => (error = err instanceof Error ? err.message : String(err)))
			.finally(() => (loading = false));
	});
</script>

<p class="back"><a href={resolve('/repositories/cadsr')}>← CDE search</a></p>

{#if loading}
	<p>Loading {page.params.id}…</p>
{:else if error}
	<p class="error">{error}</p>
{:else if cde}
	<header>
		<h1>{cde.long_name}</h1>
		<p class="meta">
			<code>{cde.public_id} v{cde.version}</code>
			{#if cde.context}<span class="tag">{cde.context}</span>{/if}
			{#if cde.datatype}<span class="tag">{cde.datatype}</span>{/if}
		</p>
		{#if cde.definition}<p class="def">{cde.definition}</p>{/if}
	</header>

	<div class="panels">
		<section>
			<h3>NCIt concepts <span class="count">({cde.concepts.length})</span></h3>
			<ul class="refs">
				{#each cde.concepts as c (c.concept_code)}
					<li>
						<a href={resolve('/repositories/ncit/[code]', { code: c.concept_code })}>
							{c.concept_name}
						</a>
						<span class="code">{c.concept_code}</span>
						{#if c.is_primary}<span class="primary">primary</span>{/if}
						{#if c.concept_type}<span class="ctype">{c.concept_type}</span>{/if}
					</li>
				{:else}
					<li class="empty">None.</li>
				{/each}
			</ul>
		</section>

		<section>
			<h3>Permissible values <span class="count">({cde.permissible_values.length})</span></h3>
			{#if cde.permissible_values.length}
				<ul class="refs">
					{#each cde.permissible_values as pv (pv.value + (pv.meaning_code ?? ''))}
						<li>
							<strong>{pv.value}</strong>
							{#if pv.meaning}— {pv.meaning}{/if}
							{#if pv.meaning_code}<span class="code">{pv.meaning_code}</span>{/if}
						</li>
					{/each}
				</ul>
			{:else}
				<p class="empty">Not an enumerated value domain.</p>
			{/if}
		</section>
	</div>
{/if}

<style>
	.back {
		margin: 0.5rem 0;
	}
	.meta {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		flex-wrap: wrap;
	}
	code {
		background: #f1f5f9;
		padding: 0.1rem 0.4rem;
		border-radius: 4px;
	}
	.tag {
		background: #ede9fe;
		color: #6d28d9;
		font-size: 0.75rem;
		padding: 0.1rem 0.5rem;
		border-radius: 999px;
	}
	.def {
		max-width: 60ch;
		color: #333;
	}
	.panels {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
		gap: 1.5rem;
		margin-top: 1.5rem;
	}
	.refs {
		list-style: none;
		padding: 0;
		margin: 0;
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		font-size: 0.88rem;
	}
	.count {
		color: #888;
		font-weight: 400;
	}
	.code {
		color: #999;
		font-size: 0.75rem;
	}
	.primary {
		background: #dcfce7;
		color: #166534;
		font-size: 0.7rem;
		padding: 0 0.4rem;
		border-radius: 999px;
	}
	.ctype {
		color: #64748b;
		font-size: 0.75rem;
	}
	.empty {
		color: #888;
		font-style: italic;
	}
	.error {
		color: #b91c1c;
	}
</style>
