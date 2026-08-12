<script lang="ts">
	import type { CdeDetail } from '$lib/types';

	let { cde }: { cde: CdeDetail } = $props();
</script>

<header class="mb-6">
	<h1 class="text-2xl font-semibold text-default">{cde.long_name}</h1>
	<div class="mt-2 flex flex-wrap items-center gap-2">
		<span class="rounded bg-subtle px-2 py-0.5 font-mono text-xs text-secondary"
			>{cde.public_id} v{cde.version}</span
		>
		{#each [cde.short_name, cde.context, cde.datatype].filter(Boolean) as label (label)}
			<span class="rounded bg-subtle px-2 py-0.5 text-xs text-muted">{label}</span>
		{/each}
	</div>
	{#if cde.definition}
		<p class="mt-3 max-w-3xl text-sm leading-relaxed text-secondary">{cde.definition}</p>
	{/if}
</header>
