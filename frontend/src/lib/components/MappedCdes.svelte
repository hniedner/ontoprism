<script lang="ts">
	import { resolve } from '$app/paths';
	import { cdesForConcept } from '$lib/api';
	import type { CdeSummary } from '$lib/types';

	let { code }: { code: string } = $props();

	let cdes = $state<CdeSummary[]>([]);
	let loaded = $state(false);

	$effect(() => {
		loaded = false;
		cdesForConcept(code, 25)
			.then((c) => (cdes = c))
			.catch(() => (cdes = []))
			.finally(() => (loaded = true));
	});
</script>

<section>
	<h3>caDSR CDEs mapping here <span class="count">({loaded ? cdes.length : '…'})</span></h3>
	{#if loaded && cdes.length === 0}
		<p class="empty">No CDEs map to this concept.</p>
	{:else}
		<ul class="refs">
			{#each cdes as cde (cde.public_id + cde.version)}
				<li>
					<a href={resolve('/repositories/cadsr/[id]', { id: cde.public_id })}>{cde.long_name}</a>
					<span class="code">{cde.public_id}</span>
				</li>
			{/each}
		</ul>
	{/if}
</section>

<style>
	h3 {
		font-size: 0.95rem;
		margin: 0 0 0.4rem;
	}
	.count {
		color: #888;
		font-weight: 400;
	}
	.refs {
		list-style: none;
		padding: 0;
		margin: 0;
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
		font-size: 0.88rem;
	}
	.code {
		color: #999;
		font-size: 0.75rem;
	}
	.empty {
		color: #888;
		font-style: italic;
		margin: 0;
	}
</style>
